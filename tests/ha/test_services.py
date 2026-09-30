"""The ``pawfly.*`` actions through Home Assistant's real service registry, against a fake light.

The service, device, entity and repairs registries and the config-entry machinery are real; only the
light is scripted. Wire facts come from ``light.events`` (decoded commands) and ``light.frames`` (the
bytes, compared with the protocol encoders). Tests that need the clock put ``freezer`` first: it
freezes the event loop's clock too, so time then moves only through ``Lamp.tick`` (never ``wait_until``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
import re
from typing import Any

import pytest
import voluptuous as vol
import yaml
from bleak.exc import BleakError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.pawfly import link as link_module
from custom_components.pawfly import protocol
from custom_components.pawfly.const import CONF_PENDING_PASSWORD, DOMAIN

from .conftest import BASE, wait_until
from .fake_light import FakePawflyLight

LIGHT, MODE, CONNECTED = f"light.{BASE}", f"sensor.{BASE}_mode", f"binary_sensor.{BASE}_connected"
OLD_KEY, NEW_KEY = "12345678", "00987654"  # the new key starts with zeros: it must stay text
ELSEWHERE = "AA:BB:CC:44:55:66"  # another light of the same integration
CHANNELS = ("red", "green", "blue", "white")
BUILT_IN = [(0, "japanese_style", "Japanese style"), (1, "dutch_style", "Dutch style"),
            (2, "jungle_style", "Jungle style")]
DIY = [(3, "diy_1", "DIY 1"), (4, "diy_2", "DIY 2"), (5, "diy_3", "DIY 3")]
MISMATCH = "program_readback_mismatch"
MISSING = object()  # a field left out of the call altogether
PACKAGE = Path(protocol.__file__).parent
MESSAGES = json.loads((PACKAGE / "translations" / "en.json").read_text())  # the texts Home Assistant shows


def _pt(time: str, red: int = 0, green: int = 0, blue: int = 0, white: int = 0) -> dict[str, Any]:
    return {"time": time, "red": red, "green": green, "blue": blue, "white": white}


def _point(spec: dict[str, Any]) -> protocol.Point:
    hour, minute = spec["time"].split(":")
    return protocol.Point(int(hour), int(minute), spec["red"], spec["green"], spec["blue"], spec["white"])


def _as_dicts(points: Any) -> list[dict[str, Any]]:
    return [_pt(f"{p.hour:02d}:{p.minute:02d}", p.red, p.green, p.blue, p.white) for p in points]


#: a program in time order, with both ends of the clock; ``SHUFFLED`` is the same one typed out of order
DAY = [_pt("00:00"), _pt("09:30", 100, 80, 60, 40), _pt("12:00", 50, 0, 0, 25), _pt("23:59", 100, 100, 100, 100)]
SHUFFLED = [DAY[3], DAY[1], DAY[0], DAY[2]]
DAY_POINTS = [
    ("program_point", 0, 0, 0, 0, 0, 0, 0),
    ("program_point", 1, 9, 30, 100, 80, 60, 40),
    ("program_point", 2, 12, 0, 50, 0, 0, 25),
    ("program_point", 3, 23, 59, 100, 100, 100, 100),
]


def _running(program: int) -> dict[str, Any]:
    return {"mode": protocol.Mode.PROGRAM, "selection": protocol.program_id(program)}


@dataclasses.dataclass
class Lamp:
    """The loaded light with its config entry and device id, and the ways a test calls its actions."""

    hass: HomeAssistant
    entry: MockConfigEntry
    light: FakePawflyLight
    id: str

    async def call(self, service: str, *, response: bool | None = None, **data: Any) -> Any:
        """Call ``pawfly.<service>`` for this light; the two actions that answer are asked for it."""
        payload = {key: value for key, value in {"device_id": self.id, **data}.items() if value is not MISSING}
        want = service in ("get_program", "set_program") if response is None else response
        return await self.hass.services.async_call(DOMAIN, service, payload, blocking=True, return_response=want)

    async def fails(self, error: type[HomeAssistantError], key: str, service: str, **data: Any) -> HomeAssistantError:
        """The action raises exactly ``error`` (a failing light is never a validation error) with ``key``."""
        with pytest.raises(error) as caught:
            await self.call(service, **data)
        assert type(caught.value) is error
        assert (caught.value.translation_domain, caught.value.translation_key) == (DOMAIN, key)
        wanted = re.findall(r"{(\w+)}", MESSAGES["exceptions"][key]["message"])  # the user reads this text: it exists ...
        assert set(wanted) <= set(caught.value.translation_placeholders or {})  # ... and every blank in it is filled in
        return caught.value

    async def tick(self, freezer: Any, seconds: float) -> None:
        """Move the clock on and run the timers that became due."""
        freezer.tick(seconds)
        async_fire_time_changed(self.hass)
        await self.hass.async_block_till_done()

    async def link_down(self) -> None:
        """The light goes out of reach: the link drops and every reconnect attempt fails."""
        self.light.connect_error = BleakError("out of range")
        self.light.drop_link()
        await wait_until(lambda: self.hass.states.get(CONNECTED).state == "off", message="the link to go down")
        self.light.clear()

    async def reconnected(self) -> None:
        """Wait until the link is up again and the light's entities are available."""
        states = self.hass.states
        await wait_until(lambda: [states.get(e).state for e in (CONNECTED, LIGHT)] == ["on", "on"], message="the light")

    def device_elsewhere(self, domain: str, identifier: str, **entry: Any) -> str:
        """The id of a device of another config entry (added, never set up)."""
        other = MockConfigEntry(domain=domain, **entry)
        other.add_to_hass(self.hass)
        registry = dr.async_get(self.hass)
        return registry.async_get_or_create(config_entry_id=other.entry_id, identifiers={(domain, identifier)}).id

    def upload(self) -> list[tuple[Any, ...]]:
        """The program-related commands the light received, in order."""
        return self.light.events_named("program_header", "program_point", "query_program", "program")

    def sent(self) -> list[bytes]:
        """Every frame written, apart from the debounced status queries."""
        return [frame for frame in self.light.frames if frame != protocol.query_status()]


@pytest.fixture
async def start(hass: HomeAssistant, setup_entry, light: FakePawflyLight):
    """``await start(mode=...)``: script the light, set its entry up, get the ``Lamp`` (empty light log)."""

    async def _start(**scripted: Any) -> Lamp:
        for name, value in scripted.items():
            setattr(light, name, value)
        entry = await setup_entry()
        registry = dr.async_get(hass)
        device = registry.async_get_device_by_identifier((DOMAIN, light.address), config_entry_id=entry.entry_id)
        assert device is not None
        light.clear()
        return Lamp(hass, entry, light, device.id)

    return _start


@pytest.fixture
async def lamp(start) -> Lamp:
    return await start()


@pytest.fixture
def short_link_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """A command gives up on a dead link after 0.2 s (the harness default is 1.5 s)."""
    monkeypatch.setattr(link_module, "COMMAND_LINK_TIMEOUT", 0.2)


#: (service, the data it needs besides device_id)
ACTIONS = [
    pytest.param("get_program", {"program": "diy_1"}, id="get_program_diy"),
    pytest.param("get_program", {"program": "dutch_style"}, id="get_program_builtin"),
    pytest.param("set_program", {"program": "diy_1", "points": []}, id="set_program"),
    pytest.param("set_channel", {"red": 10}, id="set_channel"),
    pytest.param("preview", {"red": 1}, id="preview"),
    pytest.param("sync_time", {}, id="sync_time"),
    pytest.param("rename", {"name": "Tank"}, id="rename"),
    pytest.param("change_password", {"new_password": NEW_KEY}, id="change_password"),
    pytest.param("release_link", {"resume_after": 900}, id="release_link"),  # the longest delay the schema allows
]
NEEDS_LINK = ("get_program_diy", "set_program", "set_channel", "sync_time", "rename", "change_password")


def _only(*ids: str) -> list[Any]:
    return [param for param in ACTIONS if param.id in ids]


# ---- targeting: which light, and whether it can be reached ----


@pytest.mark.parametrize(("service", "data"), ACTIONS)
async def test_unknown_device_id_is_rejected(lamp, service, data) -> None:
    await lamp.fails(ServiceValidationError, "device_not_found", service, device_id="0" * 32, **data)
    assert lamp.light.events == []


@pytest.mark.parametrize(("service", "data"), ACTIONS)
async def test_device_of_another_integration_is_rejected(lamp, service, data) -> None:
    stranger = lamp.device_elsewhere("other", "1", title="Not a light")
    await lamp.fails(ServiceValidationError, "not_a_pawfly_device", service, device_id=stranger, **data)
    assert lamp.light.events == []


@pytest.mark.parametrize(("service", "data"), _only(*NEEDS_LINK, "preview"))
async def test_unloaded_entry_refuses_actions_that_need_bluetooth(lamp, service, data) -> None:
    assert await lamp.hass.config_entries.async_unload(lamp.entry.entry_id)
    lamp.light.clear()
    error = await lamp.fails(ServiceValidationError, "entry_not_loaded", service, **data)
    assert error.translation_placeholders == {"name": lamp.entry.title}
    assert lamp.light.events == []


@pytest.mark.parametrize(("service", "data"), _only(*NEEDS_LINK))
async def test_an_unreachable_light_fails_with_not_connected(lamp, short_link_wait, service, data) -> None:
    await lamp.link_down()
    await lamp.fails(HomeAssistantError, "not_connected", service, **data)
    assert lamp.light.events == []


async def test_release_link_targets_one_light_or_all_and_tolerates_a_released_one(lamp) -> None:
    other = {"unique_id": ELSEWHERE, "data": {CONF_ADDRESS: ELSEWHERE, CONF_PASSWORD: OLD_KEY}}
    elsewhere = lamp.device_elsewhere(DOMAIN, ELSEWHERE, **other)
    await lamp.call("release_link", device_id=elsewhere, resume_after=0)
    assert lamp.entry.state is ConfigEntryState.LOADED and lamp.light.connected  # the other light's release: not this
    await lamp.call("release_link", device_id=MISSING, resume_after=0)  # no device: every light that is held
    assert lamp.entry.state is ConfigEntryState.NOT_LOADED and not lamp.light.connected
    await lamp.call("release_link", resume_after=0)  # already released: nothing to do, and no error


async def test_the_registered_actions_are_the_declared_and_translated_ones(lamp) -> None:
    declared = set(yaml.safe_load((PACKAGE / "services.yaml").read_text()))
    assert set(lamp.hass.services.async_services_for_domain(DOMAIN)) == declared == set(MESSAGES["services"])


# ---- schemas: bad input is refused before anything is written or changed ----

_TWELVE = [_pt(f"{hour:02d}:00") for hour in range(6, 18)]
BAD_POINTS = {
    "13 points": [*_TWELVE, _pt("18:00")],
    "duplicate time": [_pt("07:00"), _pt("08:00"), _pt("07:00", 5)],
    "time 7:00": [_pt("7:00")],
    "time 24:00": [_pt("24:00")],
    "time 12:60": [_pt("12:60")],
    "time noon": [_pt("noon")],
    "time with seconds": [_pt("07:00:30")],
    "time missing": [{"red": 1}],
    "red 101": [_pt("07:00", red=101)],
    "green -1": [_pt("07:00", green=-1)],
    "blue True": [_pt("07:00", blue=True)],
    "white x": [_pt("07:00", white="x")],
    "red 50.5": [_pt("07:00", red=50.5)],
    "green infinity": [_pt("07:00", green=float("inf"))],
    "unknown key": [{**_pt("07:00"), "brightness": 5}],
    "point not a mapping": ["07:00"],
    "points a string": "07:00",
    "points None": None,
}
BAD_PREVIEWS = {
    "colours and end": {"red": 10, "end": True},
    "neither": {},
    "end false alone": {"end": False},
    "red 101": {"red": 101},
    "green -1": {"green": -1},
    "blue 50.5": {"blue": 50.5},
    "white True": {"white": True},
    "red x": {"red": "x"},
    "red infinity": {"red": float("inf")},
    "red 1e999": {"red": "1e999"},
    "unknown key": {"red": 1, "brightness": 5},
}
PROGRAMS = (6, -1, "diy_9", "six", "6", True, False, None)
PASSWORDS = ("1234567", "123456789", "abcdefgh", "1234 678", "", None)
REJECTED = [
    *(pytest.param("get_program", {"program": p}, id=f"get_program {p!r}") for p in PROGRAMS),
    *(pytest.param("set_program", {"program": "diy_1", "points": pts}, id=f"set_program {why}")
      for why, pts in BAD_POINTS.items()),
    *(pytest.param("preview", data, id=f"preview {why}") for why, data in BAD_PREVIEWS.items()),
    *(pytest.param("rename", {"name": n}, id=f"rename {n!r}") for n in ("", "A" * 17, "é" * 17)),
    *(pytest.param("change_password", {"new_password": p}, id=f"change_password {p!r}") for p in PASSWORDS),
    *(pytest.param("release_link", {"resume_after": r}, id=f"release_link {r!r}") for r in (-1, 901, "soon", None)),
    # every field the schema requires, left out one at a time
    *(pytest.param(p.values[0], {**p.values[1], field: MISSING}, id=f"{p.id} without {field}")
      for p in ACTIONS if p.id not in ("get_program_builtin", "release_link") for field in ("device_id", *p.values[1])),
]


@pytest.mark.parametrize(("service", "data"), REJECTED)
async def test_the_schema_rejects_bad_input_before_anything_happens(lamp, service, data) -> None:
    with pytest.raises(vol.Invalid):
        await lamp.call(service, **data)
    assert lamp.light.events == []  # nothing reached the light ...
    assert lamp.entry.state is ConfigEntryState.LOADED and lamp.entry.data[CONF_PASSWORD] == OLD_KEY  # ... or changed


# ---- get_program ----


@pytest.mark.parametrize(
    ("form", "situation"),
    [("index", "ready"), ("digits", "ready"), ("slug", "ready"), ("slug", "entry_unloaded"), ("slug", "link_down")],
)
@pytest.mark.parametrize(("index", "slug", "name"), BUILT_IN)
async def test_get_program_serves_the_apps_curve_for_a_built_in_style(lamp, index, slug, name, form, situation) -> None:
    if situation == "entry_unloaded":
        assert await lamp.hass.config_entries.async_unload(lamp.entry.entry_id)
        lamp.light.clear()
    elif situation == "link_down":
        await lamp.link_down()
    answer = await lamp.call("get_program", program={"index": index, "digits": str(index), "slug": slug}[form])
    assert answer == {
        "program": index,
        "slug": slug,
        "name": name,
        "source": "preset",
        "editable": False,
        "points": _as_dicts(protocol.PRESETS[index]),
    }
    assert json.loads(json.dumps(answer)) == answer  # plain JSON: what the websocket API can carry
    assert lamp.light.events == []  # not a byte to the light, whatever state it is in


@pytest.mark.parametrize("form", ["index", "slug"])
@pytest.mark.parametrize(("index", "slug", "name"), DIY)
async def test_get_program_reads_an_empty_diy_slot_from_the_light(lamp, index, slug, name, form) -> None:
    answer = await lamp.call("get_program", program=index if form == "index" else slug)
    assert answer == {"program": index, "slug": slug, "name": name, "source": "device", "editable": True, "points": []}
    assert lamp.light.events == [("query_program", index)]


@pytest.mark.parametrize("concatenated", [False, True], ids=["frame_per_notification", "one_notification"])
async def test_get_program_returns_each_slots_own_points(lamp, concatenated) -> None:
    light = lamp.light
    light.concatenate_program_reply = concatenated
    light.programs[3] = [protocol.Point(7, 5, 1, 2, 3, 4)]
    light.programs[4] = [_point(spec) for spec in DAY]
    light.programs[5] = [protocol.Point(hour, 30, hour, 100 - hour, 2 * hour, 5) for hour in range(6, 18)]  # a full 12
    for index, slug, _name in DIY:
        answer = await lamp.call("get_program", program=slug)
        assert (answer["source"], answer["points"]) == ("device", _as_dicts(light.programs[index]))


async def test_get_program_from_a_light_that_never_answers_fails_after_a_bounded_wait(lamp) -> None:
    lamp.light.silent = True
    await lamp.fails(HomeAssistantError, "command_failed", "get_program", program="diy_2")
    assert set(lamp.light.events) == {("query_program", 4)}


async def test_get_program_needs_a_response_request_and_set_program_does_not(lamp) -> None:
    with pytest.raises(ServiceValidationError):
        await lamp.call("get_program", response=False, program="diy_1")
    assert lamp.light.events == []
    assert await lamp.call("set_program", response=False, program="diy_1", points=DAY) is None
    assert lamp.light.programs[3] == [_point(spec) for spec in DAY]
    assert (await lamp.call("set_program", program="diy_1", points=DAY))["verified"] is True


# ---- set_program ----


@pytest.mark.parametrize("form", ["index", "slug"])
@pytest.mark.parametrize(("index", "slug", "name"), DIY)
async def test_set_program_writes_reads_back_and_answers_with_the_read_back(lamp, index, slug, name, form) -> None:
    answer = await lamp.call("set_program", program=index if form == "index" else slug, points=SHUFFLED)
    assert answer == {
        "program": index,
        "slug": slug,
        "name": name,
        "source": "device",
        "editable": True,
        "count": 4,
        "verified": True,
        "points": DAY,  # in time order, whatever order they were given in
    }
    assert lamp.upload() == [("program_header", index, 4), *DAY_POINTS, ("query_program", index)]
    stored = [_point(spec) for spec in DAY]
    assert lamp.sent() == [*protocol.program_upload(index, stored), protocol.query_program(index)]
    assert lamp.light.programs[index] == stored
    assert (await lamp.call("get_program", program=slug))["points"] == DAY


async def test_set_program_with_no_points_clears_the_slot(lamp) -> None:
    lamp.light.programs[4] = [_point(spec) for spec in DAY]
    answer = await lamp.call("set_program", program="diy_2", points=[])
    assert (answer["count"], answer["points"], answer["verified"]) == (0, [], True)
    assert lamp.upload() == [("program_header", 4, 0), ("query_program", 4)]
    assert lamp.light.programs[4] == []
    assert (await lamp.call("get_program", program=4))["points"] == []


async def test_set_program_takes_twelve_points_and_whole_number_floats(lamp) -> None:
    points = [_pt(f"{hour:02d}:15", hour, 100 - hour, hour, 100) for hour in range(6, 18)]
    floats = [{**point, "red": float(point["red"]), "white": 100.0} for point in points]
    answer = await lamp.call("set_program", program="diy_3", points=floats)
    assert (answer["count"], answer["points"]) == (12, points)
    assert all(type(point[channel]) is int for point in answer["points"] for channel in CHANNELS)
    assert lamp.light.events_named("program_header") == [("program_header", 5, 12)]


@pytest.mark.parametrize(
    ("program", "name"),
    [(0, "Japanese style"), ("1", "Dutch style"), ("jungle_style", "Jungle style"), ("dutch_style", "Dutch style")],
)
async def test_set_program_refuses_the_built_in_styles(lamp, program, name) -> None:
    error = await lamp.fails(ServiceValidationError, "program_read_only", "set_program", program=program, points=DAY)
    assert error.translation_placeholders == {"program": name}
    assert lamp.light.events == []


def _keeps(alter):
    """A faulty light: it accepts an upload but keeps what ``alter`` makes of the points."""

    def misbehave(light: FakePawflyLight, monkeypatch: pytest.MonkeyPatch) -> None:
        def finish() -> None:
            upload, light._upload = light._upload, None
            light.programs[upload["program"]] = alter(upload["points"])

        monkeypatch.setattr(light, "_finish_upload", finish)

    return misbehave


def _bumped(points: list[protocol.Point]) -> list[protocol.Point]:
    return [*points[:-1], dataclasses.replace(points[-1], white=points[-1].white + 1)]


def _shifted(points: list[protocol.Point]) -> list[protocol.Point]:
    return [dataclasses.replace(point, minute=(point.minute + 1) % 60) for point in points]


#: fault -> (how the light misbehaves, the error the caller gets, how many points the read-back found)
FAULTS = {
    "ignored": (lambda light, _mp: setattr(light, "store_programs", False), MISMATCH, "1"),
    "lost_point": (_keeps(lambda points: points[:-1]), MISMATCH, "3"),
    "changed_value": (_keeps(_bumped), MISMATCH, "4"),
    "shifted_time": (_keeps(_shifted), MISMATCH, "4"),
    "no_answer": (lambda light, _mp: setattr(light, "silent", True), "command_failed", None),
}


@pytest.mark.parametrize("running", [False, True], ids=["idle", "program_running"])
@pytest.mark.parametrize("fault", list(FAULTS))
async def test_set_program_never_reports_or_activates_an_unverified_upload(start, monkeypatch, fault, running) -> None:
    lamp = await start(**(_running(4) if running else {}))
    lamp.light.programs[4] = [protocol.Point(12, 0, 1, 1, 1, 1)]
    misbehave, key, found = FAULTS[fault]
    misbehave(lamp.light, monkeypatch)
    error = await lamp.fails(HomeAssistantError, key, "set_program", program="diy_2", points=DAY)
    if found is not None:
        assert (error.translation_placeholders["expected"], error.translation_placeholders["found"]) == ("4", found)
    assert lamp.light.events_named("program") == []  # the running program is not restarted on unverified points


@pytest.mark.parametrize(
    ("script", "written", "restarted"),
    [
        (_running(3), 3, True),
        (_running(4), 4, True),
        (_running(5), 5, True),
        ({}, 4, False),  # a manual colour
        ({"mode": protocol.Mode.SCENARIO, "selection": 4}, 4, False),  # a scene
        (_running(5), 4, False),  # another DIY slot
        (_running(0), 4, False),  # a built-in style
    ],
    ids=["diy_1", "diy_2", "diy_3", "manual", "scene", "other_slot", "built_in"],
)
async def test_set_program_restarts_only_the_program_it_rewrote(start, script, written, restarted) -> None:
    lamp = await start(**script)
    light = lamp.light
    before = (light.mode, light.selection)
    await lamp.call("set_program", program=written, points=DAY)
    expected = [("program_header", written, 4), *DAY_POINTS, ("query_program", written)]
    assert lamp.upload() == [*expected, *([("program", written)] if restarted else [])]
    if restarted:  # once the read-back proved the points, and with the light's own id for the slot
        upload = protocol.program_upload(written, [_point(spec) for spec in DAY])
        assert lamp.sent() == [*upload, protocol.query_program(written), protocol.program(written)]
        await wait_until(lambda: light.events[-1] == ("query_status",), message="the status read-back")
    assert (light.mode, light.selection) == before


async def test_set_program_fails_when_the_link_is_lost_in_the_middle_of_the_upload(lamp, monkeypatch) -> None:
    light = lamp.light
    original = light.on_write

    async def lose_the_link_on_the_third_frame(data: bytes) -> None:
        await original(data)
        if len(light.events_named("program_header", "program_point")) == 3:
            light.drop_link()

    monkeypatch.setattr(light, "on_write", lose_the_link_on_the_third_frame)
    await lamp.fails(HomeAssistantError, "not_connected", "set_program", program="diy_1", points=DAY)
    assert [event[0] for event in lamp.upload()] == ["program_header", "program_point", "program_point"]


# ---- preview ----


@pytest.mark.parametrize(
    ("data", "shown"),
    [
        ({"red": 10, "green": 20, "blue": 30, "white": 40}, (10, 20, 30, 40)),
        ({"blue": 100}, (0, 0, 100, 0)),  # channels not given are 0
        ({"white": 0}, (0, 0, 0, 0)),  # zero is a colour (black), not "no colour"
        ({"green": 50.0, "end": False}, (0, 50, 0, 0)),
        ({"end": True}, None),
    ],
    ids=["all_channels", "one_channel", "black", "whole_float", "end"],
)
async def test_preview_writes_one_frame_and_neither_queries_nor_changes_the_mode(freezer, start, data, shown) -> None:
    lamp = await start(**_running(4))
    await lamp.call("preview", **data)
    await lamp.tick(freezer, 1)  # a status query scheduled by the preview would run now
    assert lamp.light.events == [("preview", shown)]
    assert lamp.hass.states.get(MODE).state == "program"


async def test_a_burst_of_previews_is_coalesced_and_the_last_colour_wins(lamp, monkeypatch) -> None:
    original = lamp.light.on_write

    async def slow(data: bytes) -> None:
        await asyncio.sleep(0.01)
        await original(data)

    monkeypatch.setattr(lamp.light, "on_write", slow)
    colours = [(i, 100 - i, i, 0) for i in range(20)]
    await asyncio.gather(*(lamp.call("preview", red=r, green=g, blue=b, white=w) for r, g, b, w in colours))
    shown = [event[1] for event in lamp.light.events_named("preview")]
    assert len(shown) <= 5  # far fewer than the 20 requests
    assert shown[-1] == colours[-1]
    assert set(shown) <= set(colours)
    await lamp.call("preview", end=True)


async def test_ending_a_preview_returns_only_after_the_end_frame_was_written(lamp, monkeypatch) -> None:
    original = lamp.light.on_write
    started, release = asyncio.Event(), asyncio.Event()

    async def held(data: bytes) -> None:
        started.set()
        await release.wait()
        await original(data)

    monkeypatch.setattr(lamp.light, "on_write", held)
    drawing = asyncio.create_task(lamp.call("preview", red=10))
    await asyncio.wait_for(started.wait(), 3)
    ending = asyncio.create_task(lamp.call("preview", end=True))
    done, _pending = await asyncio.wait({ending}, timeout=0.1)
    assert not done  # queued behind the write in flight
    release.set()
    await ending
    assert lamp.light.events_named("preview") == [("preview", (10, 0, 0, 0)), ("preview", None)]
    await drawing


def _fail_preview_writes(lamp: Lamp, monkeypatch: pytest.MonkeyPatch, *, only: bytes | None = None) -> asyncio.Event:
    """Preview frames (only ``only`` when given) fail after a moment; the event is set when the first one starts."""
    original, started = lamp.light.on_write, asyncio.Event()

    async def flaky(data: bytes) -> None:
        if data[2] == protocol.CMD_PREVIEW and only in (None, data):
            started.set()
            await asyncio.sleep(0.05)
            raise BleakError("the write failed")
        await original(data)

    monkeypatch.setattr(lamp.light, "on_write", flaky)
    return started


async def test_ending_a_preview_sends_the_end_frame_itself_when_the_write_it_waited_for_failed(lamp, monkeypatch) -> None:
    """The end request queues behind the write in flight; when that write fails nobody else is left to
    send it, so the caller must (it used to return success with no end frame on the wire)."""
    started = _fail_preview_writes(lamp, monkeypatch, only=protocol.preview((10, 0, 0, 0)))
    drawing = asyncio.create_task(lamp.call("preview", red=10))
    await asyncio.wait_for(started.wait(), 3)
    ending = asyncio.create_task(lamp.call("preview", end=True))

    with pytest.raises(HomeAssistantError):  # the drawing call reports its own failed write
        await drawing
    await ending  # ... and the end call reports success only because its own frame went out

    assert lamp.light.events_named("preview") == [("preview", None)]


async def test_ending_a_preview_reports_the_failure_when_the_end_frame_cannot_be_written_either(lamp, monkeypatch) -> None:
    """Two end requests wait behind the failed write: each one tries its own frame, and none may report
    success for a frame that never reached the light (the second used to find its request already taken)."""
    started = _fail_preview_writes(lamp, monkeypatch)
    drawing = asyncio.create_task(lamp.call("preview", red=10))
    await asyncio.wait_for(started.wait(), 3)
    endings = [asyncio.create_task(lamp.call("preview", end=True)) for _ in range(2)]

    with pytest.raises(HomeAssistantError):
        await drawing
    for ending in endings:
        with pytest.raises(HomeAssistantError):
            await ending
    assert lamp.light.events_named("preview") == []


async def test_a_preview_nobody_refreshes_is_ended_once_by_the_watchdog(freezer, lamp) -> None:
    await lamp.call("preview", red=10)
    await lamp.tick(freezer, 29)
    assert lamp.light.events == [("preview", (10, 0, 0, 0))]
    await lamp.tick(freezer, 2)
    assert lamp.light.events == [("preview", (10, 0, 0, 0)), ("preview", None)]
    await lamp.tick(freezer, 25)
    assert lamp.light.events_named("preview") == [("preview", (10, 0, 0, 0)), ("preview", None)]


async def test_refreshing_a_preview_postpones_the_watchdog(freezer, lamp) -> None:
    await lamp.call("preview", red=10)
    await lamp.tick(freezer, 20)
    await lamp.call("preview", red=20)
    await lamp.tick(freezer, 20)  # 40 s after the first frame, only 20 after the last
    assert lamp.light.events_named("preview") == [("preview", (10, 0, 0, 0)), ("preview", (20, 0, 0, 0))]
    await lamp.tick(freezer, 11)  # 31 s after the last
    assert lamp.light.events_named("preview")[2:] == [("preview", None)]


async def test_an_ended_preview_leaves_no_watchdog_behind(freezer, lamp) -> None:
    await lamp.call("preview", red=10)
    await lamp.call("preview", end=True)
    await lamp.tick(freezer, 45)
    assert lamp.light.events_named("preview") == [("preview", (10, 0, 0, 0)), ("preview", None)]


async def test_unloading_ends_an_active_preview_before_disconnecting(lamp) -> None:
    await lamp.call("preview", white=100)
    assert await lamp.hass.config_entries.async_unload(lamp.entry.entry_id)
    events = lamp.light.events
    assert events[:2] == [("preview", (0, 0, 0, 100)), ("preview", None)]  # the end frame goes out first ...
    assert events[-1] == ("disconnect",)  # ... and the link is dropped last


async def test_preview_gives_up_quickly_on_an_unreachable_light_and_works_again_after(lamp, monkeypatch) -> None:
    monkeypatch.setattr(link_module, "COMMAND_LINK_TIMEOUT", 30.0)  # a preview must not wait for this
    await lamp.link_down()
    async with asyncio.timeout(5):
        await lamp.fails(HomeAssistantError, "not_connected", "preview", red=10)
    assert lamp.light.events == []
    lamp.light.connect_error = None
    await lamp.reconnected()
    await lamp.call("preview", red=5)
    assert lamp.light.events_named("preview") == [("preview", (5, 0, 0, 0))]
    await lamp.call("preview", end=True)


# ---- sync_time, rename, change_password ----


@pytest.mark.freeze_time("2035-03-14 23:04:05")
async def test_sync_time_sends_home_assistants_local_time(freezer, lamp) -> None:
    await lamp.hass.config.async_set_time_zone("Asia/Kolkata")  # 04:34:05 on a Thursday there, 23:04:05 Wednesday UTC
    await lamp.call("sync_time")
    assert lamp.light.events == [("time_sync", 4, 34, 5, 4)]
    await lamp.tick(freezer, 1)
    assert lamp.light.events[-1] == ("query_status",)  # like every command, it is read back


@pytest.mark.parametrize(
    ("typed", "sent"),
    [
        ("Tank", "Tank"),
        ("A" * 16, "A" * 16),
        ("Reef Tank #2", "Reef Tank #2"),
        ("PY4C-Tank", "Tank"),  # a typed prefix is dropped: the light keeps its own
        ("PYLamp-4C_Reef", "Reef"),
    ],
)
async def test_rename_sends_the_display_name(lamp, typed, sent) -> None:
    await lamp.call("rename", name=typed)
    assert lamp.light.events == [("rename", sent)]


@pytest.mark.parametrize("name", ["   ", "\t", "PY4C-", "Café", "Tank\n"])
async def test_rename_refuses_a_blank_or_non_ascii_name(lamp, name) -> None:
    await lamp.fails(ServiceValidationError, "invalid_name", "rename", name=name)
    assert lamp.light.events == []


async def test_change_password_saves_the_key_only_after_the_light_accepted_it_on_a_new_link(lamp, monkeypatch) -> None:
    light, entry = lamp.light, lamp.entry
    saved_when_asked: list[str] = []
    original = light.on_write

    async def spy(data: bytes) -> None:
        await original(data)
        if light.events[-1:] == [("verify_key", NEW_KEY)]:
            saved_when_asked.append(entry.data[CONF_PASSWORD])

    monkeypatch.setattr(light, "on_write", spy)
    await lamp.call("change_password", new_password=NEW_KEY)
    wire = [event for event in light.events if event[0] in ("change_key", "disconnect", "verify_key")]
    assert wire == [("change_key", NEW_KEY), ("disconnect",), ("verify_key", NEW_KEY)]
    assert light.key == NEW_KEY
    assert saved_when_asked == [OLD_KEY]  # still the old key while the light was being asked
    assert entry.data[CONF_PASSWORD] == NEW_KEY
    await lamp.reconnected()
    assert lamp.hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert await lamp.hass.config_entries.async_reload(entry.entry_id)  # the saved key opens the light after a restart
    assert entry.state is ConfigEntryState.LOADED
    assert light.events_named("verify_key")[-1] == ("verify_key", NEW_KEY)


async def test_change_password_the_light_ignores_leaves_the_old_key_in_force_and_asks_for_no_reauth(lamp) -> None:
    light, entry = lamp.light, lamp.entry
    light.apply_key_change = False
    error = await lamp.fails(HomeAssistantError, "password_not_accepted", "change_password", new_password=NEW_KEY)
    assert error.translation_placeholders == {"name": entry.title}
    assert entry.data[CONF_PASSWORD] == OLD_KEY and light.key == OLD_KEY
    keys = [event for event in light.events if event[0] in ("change_key", "verify_key")]
    assert keys == [("change_key", NEW_KEY), ("verify_key", NEW_KEY), ("verify_key", OLD_KEY)]  # back on the old key
    await lamp.reconnected()
    await lamp.hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert lamp.hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


# ---- change_password: what a slow or refused proof of the new key must never do ----


def _keys(light: FakePawflyLight) -> list[tuple[Any, ...]]:
    return [event for event in light.events if event[0] in ("change_key", "verify_key")]


def _reauth(lamp: Lamp) -> list[Any]:
    return list(lamp.entry.async_get_active_flows(lamp.hass, {SOURCE_REAUTH}))


async def test_a_slow_reconnect_after_a_key_change_keeps_the_new_key_and_settles_when_the_light_answers(
    lamp, monkeypatch
) -> None:
    """The light took the new key, but the link does not come back within the wait. The old key must not be
    restored (the light would refuse it); the new key stays pending, is proved on the next connect and saved."""
    light, entry = lamp.light, lamp.entry
    monkeypatch.setattr(link_module, "RELINK_TIMEOUT", 0.4)  # over before the slow reconnect is
    light.connect_delay = 1.0

    error = await lamp.fails(HomeAssistantError, "password_change_unconfirmed", "change_password", new_password=NEW_KEY)

    assert error.translation_placeholders == {"name": entry.title}
    assert (entry.data[CONF_PASSWORD], entry.data[CONF_PENDING_PASSWORD]) == (OLD_KEY, NEW_KEY)  # kept over a reload
    assert lamp.hass.states.get(CONNECTED).state == "off"  # the entities follow the link while it is rebuilt
    light.connect_delay = 0
    await lamp.reconnected()
    assert entry.data[CONF_PASSWORD] == NEW_KEY and CONF_PENDING_PASSWORD not in entry.data
    assert _keys(light) == [("change_key", NEW_KEY), ("verify_key", NEW_KEY)]  # the old key was never tried again
    assert _reauth(lamp) == []


async def test_a_key_change_the_light_never_applied_settles_back_on_the_old_key(lamp, monkeypatch) -> None:
    """A pending key is only ever a candidate: when the light explicitly refuses it, the old one goes on."""
    light, entry = lamp.light, lamp.entry
    monkeypatch.setattr(link_module, "RELINK_TIMEOUT", 0.4)
    light.apply_key_change = False
    light.connect_delay = 1.0

    await lamp.fails(HomeAssistantError, "password_change_unconfirmed", "change_password", new_password=NEW_KEY)
    light.connect_delay = 0
    await lamp.reconnected()

    assert _keys(light) == [("change_key", NEW_KEY), ("verify_key", NEW_KEY), ("verify_key", OLD_KEY)]
    assert entry.data[CONF_PASSWORD] == OLD_KEY and CONF_PENDING_PASSWORD not in entry.data
    assert _reauth(lamp) == []


async def test_a_key_that_settled_just_as_the_wait_ran_out_leaves_nothing_pending(lamp, monkeypatch) -> None:
    """The session opened and the key was saved a moment before the caller gave up waiting: the saved
    password must not also be kept as a pending one."""
    coordinator = lamp.entry.runtime_data

    async def settled_then_too_late(new_password: str) -> None:
        coordinator._store_password(new_password)  # noqa: SLF001 - what the link does when the session opens
        raise link_module.ChangeUnconfirmed("the wait ended just after")

    monkeypatch.setattr(coordinator.link, "async_change_password", settled_then_too_late)
    await lamp.fails(HomeAssistantError, "password_change_unconfirmed", "change_password", new_password=NEW_KEY)

    assert lamp.entry.data[CONF_PASSWORD] == NEW_KEY and CONF_PENDING_PASSWORD not in lamp.entry.data


@pytest.mark.parametrize(
    ("light_key", "tried"),
    [(NEW_KEY, [NEW_KEY]), (OLD_KEY, [NEW_KEY, OLD_KEY])],
    ids=["applied", "never_applied"],
)
async def test_a_pending_key_saved_before_a_reload_is_tried_first_and_then_settled(
    hass, setup_entry, light, light_key, tried
) -> None:
    light.key = light_key
    entry = await setup_entry(data={CONF_PENDING_PASSWORD: NEW_KEY})

    assert [event[1] for event in light.events_named("verify_key")] == tried
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_PASSWORD] == light_key and CONF_PENDING_PASSWORD not in entry.data
    assert list(entry.async_get_active_flows(hass, {SOURCE_REAUTH})) == []


async def test_when_neither_key_is_accepted_the_pending_key_is_forgotten_and_reauth_starts(
    hass, setup_entry, light
) -> None:
    light.key = "55555555"  # the light wants a third key
    entry = await setup_entry(data={CONF_PENDING_PASSWORD: NEW_KEY})

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert CONF_PENDING_PASSWORD not in entry.data and entry.data[CONF_PASSWORD] == OLD_KEY
    assert [event[1] for event in light.events_named("verify_key")] == [NEW_KEY, OLD_KEY]
    assert len(list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))) == 1
