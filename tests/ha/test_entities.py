"""Entities of the Pawfly light through real Home Assistant, with a fake BLE light.

Covers the registry (ids, names, categories, device), the mapping from the light's status to
entity states, push updates, availability, and every command through the real services with the
exact frames that reach the wire.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from bleak.exc import BleakError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.pawfly import coordinator as coordinator_module
from custom_components.pawfly import model, protocol
from custom_components.pawfly.const import DEFAULT_RESUME_AFTER, DOMAIN

from .conftest import BASE, ENTRY_TITLE, wait_until
from .fake_light import PROXY_ADAPTER, TEST_ADDRESS

LIGHT = f"light.{BASE}"
PROGRAM = f"select.{BASE}_program"
MODE = f"sensor.{BASE}_mode"
CONNECTED = f"binary_sensor.{BASE}_connected"
RELEASE = f"button.{BASE}_release_link"
SYNC_TIME = f"button.{BASE}_sync_time"

#: The whole entity set: id -> (unique id, friendly name, entity category).
ENTITIES = {
    LIGHT: ("AABBCC112233_light", ENTRY_TITLE, None),
    PROGRAM: ("AABBCC112233_program", f"{ENTRY_TITLE} Program", None),
    MODE: ("AABBCC112233_mode", f"{ENTRY_TITLE} Mode", EntityCategory.DIAGNOSTIC),
    CONNECTED: ("AABBCC112233_connected", f"{ENTRY_TITLE} Connected", EntityCategory.DIAGNOSTIC),
    RELEASE: ("AABBCC112233_release_link", f"{ENTRY_TITLE} Release link", EntityCategory.DIAGNOSTIC),
    SYNC_TIME: ("AABBCC112233_sync_time", f"{ENTRY_TITLE} Sync time", EntityCategory.CONFIG),
}
SCENES = ("Cloudy", "Thunderstorm", "Sunny", "Moonlight", "Warm White", "Bright White", "Day", "RGB Cycle")
PROGRAM_OPTIONS = ("japanese_style", "dutch_style", "jungle_style", "diy_1", "diy_2", "diy_3")


def _ha(percent: int) -> int:
    """The documented colour scale: device 0-100 % -> HA 0-255."""
    return round(percent * 255 / 100)


async def _push(hass, light, **fields) -> None:
    """Change what the light reports and let it announce that unasked."""
    for name, value in fields.items():
        setattr(light, name, value)
    light.push_status()
    await hass.async_block_till_done()


async def _call(hass, domain: str, service: str, entity_id: str, **data) -> None:
    await hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True)


async def _read_back(hass, light) -> None:
    """Wait for the status query that follows a command, then let the entities update."""
    await wait_until(lambda: light.events_named("query_status"), message="the status query after the command")
    await hass.async_block_till_done()


def _scene(light, index: int) -> None:
    light.mode, light.selection = protocol.Mode.SCENARIO, index


def _program(light, index: int) -> None:
    light.mode, light.selection = protocol.Mode.PROGRAM, protocol.program_id(index)


# ---------------------------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------------------------


async def test_entity_set_ids_names_and_categories(hass, config_entry) -> None:
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert {e.entity_id: (e.unique_id, e.entity_category) for e in entries} == {
        entity_id: (uid, category) for entity_id, (uid, _name, category) in ENTITIES.items()
    }
    for entity_id, (_uid, name, _category) in ENTITIES.items():
        if entity_id != SYNC_TIME:  # disabled: no state, checked below
            assert hass.states.get(entity_id).attributes["friendly_name"] == name
    assert all(":" not in e.unique_id for e in entries)


async def test_sync_time_is_disabled_by_the_integration(hass, config_entry) -> None:
    entry = er.async_get(hass).async_get(SYNC_TIME)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(SYNC_TIME) is None


async def test_device_info(hass, config_entry) -> None:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, TEST_ADDRESS), config_entry.entry_id)
    assert device is not None
    assert device.name == ENTRY_TITLE
    assert device.manufacturer == "Pawfly"
    assert (CONNECTION_BLUETOOTH, TEST_ADDRESS) in device.connections
    registry = er.async_get(hass)
    assert {e.device_id for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)} == {device.id}


async def test_ids_survive_a_reload(hass, config_entry) -> None:
    registry = er.async_get(hass)
    before = {e.entity_id: e.unique_id for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)}
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    after = {e.entity_id: e.unique_id for e in er.async_entries_for_config_entry(registry, config_entry.entry_id)}
    assert after == before
    assert hass.states.get(LIGHT).state == "on"


# ---------------------------------------------------------------------------------------------
# status -> state
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("percent", "ha_value"), [(1, 3), (50, 128), (90, 230), (100, 255)])
async def test_manual_brightness_mapping(hass, config_entry, light, percent, ha_value) -> None:
    await _push(hass, light, brightness=percent)
    state = hass.states.get(LIGHT)
    assert state.state == "on"
    assert state.attributes["brightness"] == ha_value
    assert hass.states.get(MODE).state == "manual"
    assert hass.states.get(PROGRAM).state == "manual"


@pytest.mark.parametrize(
    ("rgbw", "expected"),
    [
        ([0, 100, 0, 0], (0, 255, 0, 0)),
        ([100, 0, 0, 100], (255, 0, 0, 255)),
        ([20, 40, 60, 80], (51, 102, 153, 204)),
        ([1, 2, 3, 4], (3, 5, 8, 10)),
        ([50, 0, 10, 0], (128, 0, 26, 0)),
    ],
)
async def test_manual_colour_mapping(hass, config_entry, light, rgbw, expected) -> None:
    await _push(hass, light, rgbw=rgbw)
    assert tuple(hass.states.get(LIGHT).attributes["rgbw_color"]) == expected


def test_conversions_round_trip_for_every_device_value() -> None:
    assert [model.channel_to_device(model.channel_to_ha(p)) for p in range(101)] == list(range(101))
    assert [model.brightness_to_device(model.brightness_to_ha(p)) for p in range(1, 101)] == list(range(1, 101))
    assert [model.channel_to_ha(p) for p in range(101)] == sorted({model.channel_to_ha(p) for p in range(101)})


async def test_power_off(hass, config_entry, light) -> None:
    await _push(hass, light, power=False)
    assert hass.states.get(LIGHT).state == "off"
    assert hass.states.get(PROGRAM).state == "manual"


@pytest.mark.parametrize("index", range(8))
async def test_scene_state(hass, config_entry, light, index) -> None:
    light.rgbw = [20, 40, 60, 80]  # the manual register: a scene must not show it
    _scene(light, index)
    await _push(hass, light)
    attrs = hass.states.get(LIGHT).attributes
    assert attrs["effect"] == SCENES[index]
    assert tuple(attrs["rgbw_color"]) == tuple(_ha(p) for p in protocol.SCENARIO_COLORS[index])
    assert (attrs["mode"], attrs["scenario"]) == ("scene", index)
    assert hass.states.get(PROGRAM).state == "unknown"
    assert hass.states.get(MODE).state == "scene"


@pytest.mark.parametrize(
    ("index", "slug", "name"),
    [
        (0, "japanese_style", "Japanese style"),
        (1, "dutch_style", "Dutch style"),
        (2, "jungle_style", "Jungle style"),
        (3, "diy_1", "DIY 1"),
        (4, "diy_2", "DIY 2"),
        (5, "diy_3", "DIY 3"),
    ],
)
async def test_program_state(hass, config_entry, light, index, slug, name) -> None:
    light.rgbw = [20, 40, 60, 80]
    _program(light, index)
    await _push(hass, light)
    assert light.selection == (index if index < 3 else index + 125)  # DIY programs travel as 128-130
    state = hass.states.get(LIGHT)
    attrs = state.attributes
    assert (attrs["mode"], attrs["program"], attrs["program_name"]) == ("program", index, name)
    assert attrs["effect"] is None
    assert tuple(attrs["rgbw_color"]) == (51, 102, 153, 204)  # programs keep showing the register
    assert hass.states.get(PROGRAM).state == slug
    assert hass.states.get(MODE).state == "program"


async def test_light_offers_the_scenes_and_demo_as_effects(hass, config_entry) -> None:
    assert hass.states.get(LIGHT).attributes["effect_list"] == [*SCENES, "Demo"]


# ---------------------------------------------------------------------------------------------
# push updates
# ---------------------------------------------------------------------------------------------


async def test_unsolicited_status_updates_without_any_command(hass, config_entry, light) -> None:
    light.clear()
    await _push(hass, light, brightness=40)
    assert hass.states.get(LIGHT).attributes["brightness"] == 102
    await asyncio.sleep(0.1)  # ten debounce periods: a query scheduled by the push would have fired
    assert light.events == []


async def test_identical_status_causes_no_state_churn(hass, config_entry, light, freezer) -> None:
    before = hass.states.get(LIGHT).last_updated
    freezer.tick(60)
    await _push(hass, light)
    assert hass.states.get(LIGHT).last_updated == before
    await _push(hass, light, brightness=40)  # sanity: a real change does move it
    assert hass.states.get(LIGHT).last_updated > before


# ---------------------------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------------------------


async def test_link_loss_makes_entities_unavailable_until_it_recovers(hass, config_entry, light) -> None:
    light.connect_error = BleakError("x")  # keep the link down long enough to look at it
    light.drop_link()
    await wait_until(lambda: hass.states.get(LIGHT).state == "unavailable", message="the outage to show")
    for entity_id in (LIGHT, PROGRAM, MODE):
        assert hass.states.get(entity_id).state == "unavailable", entity_id
    connected = hass.states.get(CONNECTED)
    assert connected.state == "off"  # a diagnostic that reports the outage stays available
    light.connect_error = None
    await wait_until(lambda: hass.states.get(LIGHT).state == "on", message="the light after the reconnect")
    assert hass.states.get(LIGHT).attributes["brightness"] == 230
    assert hass.states.get(PROGRAM).state == "manual"
    await wait_until(lambda: hass.states.get(CONNECTED).state == "on", message="connected")
    attrs = hass.states.get(CONNECTED).attributes
    assert attrs["drops_1h"] == 1
    assert attrs["proxy"] == PROXY_ADAPTER == "plant-room-bluetooth-proxy"


async def test_command_without_a_link_fails_and_changes_no_state(hass, config_entry, light) -> None:
    light.connect_error = BleakError("x")
    light.drop_link()
    light.clear()
    with pytest.raises(HomeAssistantError) as raised:
        await _call(hass, "light", "turn_on", LIGHT, brightness=128)
    assert raised.value.translation_key == "not_connected"
    assert light.events_named("brightness", "power", "white", "color") == []
    light.connect_error = None
    await wait_until(lambda: hass.states.get(LIGHT).state == "on", message="the link to come back")
    assert hass.states.get(LIGHT).attributes["brightness"] == 230  # nothing was assumed while it was down
    assert light.events_named("brightness") == []


# ---------------------------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("ha_value", "percent"), [(1, 1), (3, 1), (128, 50), (230, 90), (255, 100)])
async def test_turn_on_brightness_sends_only_brightness(hass, config_entry, light, ha_value, percent) -> None:
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT, brightness=ha_value)
    assert hass.states.get(LIGHT).attributes["brightness"] == 230  # no optimistic state
    await _read_back(hass, light)
    assert light.events == [("brightness", percent), ("query_status",)]
    assert hass.states.get(LIGHT).attributes["brightness"] == model.brightness_to_ha(percent)


@pytest.mark.parametrize("running", ["scene", "program"])
async def test_brightness_keeps_the_running_mode(hass, config_entry, light, running) -> None:
    _scene(light, 2) if running == "scene" else _program(light, 4)
    await _push(hass, light)
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT, brightness=128)
    await _read_back(hass, light)
    assert light.events == [("brightness", 50), ("query_status",)]
    assert hass.states.get(MODE).state == running
    assert hass.states.get(LIGHT).attributes["effect"] == ("Sunny" if running == "scene" else None)


async def test_turn_on_while_off_powers_on_first(hass, config_entry, light) -> None:
    await _push(hass, light, power=False)
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT, brightness=128)
    await _read_back(hass, light)
    assert light.events == [("power", True), ("brightness", 50), ("query_status",)]
    assert hass.states.get(LIGHT).state == "on"


async def test_plain_turn_on_only_powers_on(hass, config_entry, light) -> None:
    await _push(hass, light, power=False)
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT)
    await _read_back(hass, light)
    assert light.events == [("power", True), ("query_status",)]


@pytest.mark.parametrize("running", ["scene", "program"])
async def test_colour_forces_manual_mode(hass, config_entry, light, running) -> None:
    _scene(light, 2) if running == "scene" else _program(light, 4)
    await _push(hass, light)
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT, rgbw_color=[255, 128, 0, 51])
    await _read_back(hass, light)
    assert light.events == [("white", 20, 3), ("color", 100, 50, 0), ("query_status",)]
    assert hass.states.get(MODE).state == "manual"
    assert hass.states.get(PROGRAM).state == "manual"
    attrs = hass.states.get(LIGHT).attributes
    assert attrs["effect"] is None
    assert tuple(attrs["rgbw_color"]) == (255, 128, 0, 51)


@pytest.mark.parametrize("index", range(8))
async def test_effect_selects_the_scene(hass, config_entry, light, index) -> None:
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT, effect=SCENES[index])
    await _read_back(hass, light)
    assert light.events == [("scenario", index), ("query_status",)]
    assert hass.states.get(LIGHT).attributes["effect"] == SCENES[index]
    assert hass.states.get(MODE).state == "scene"
    assert hass.states.get(PROGRAM).state == "unknown"


async def test_effect_wins_over_colour_but_brightness_still_applies(hass, config_entry, light) -> None:
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT, effect="Sunny", rgbw_color=[255, 0, 0, 0], brightness=128)
    await _read_back(hass, light)
    assert light.events == [("brightness", 50), ("scenario", 2), ("query_status",)]
    assert hass.states.get(MODE).state == "scene"


@pytest.mark.parametrize("running", ["manual", "scene"])
async def test_demo_needs_a_running_program(hass, config_entry, light, running) -> None:
    if running == "scene":
        _scene(light, 2)
        await _push(hass, light)
    light.clear()
    with pytest.raises(ServiceValidationError) as raised:
        await _call(hass, "light", "turn_on", LIGHT, effect="Demo")
    assert raised.value.translation_key == "demo_needs_program"
    assert light.events == []


async def test_demo_is_sent_while_a_program_runs(hass, config_entry, light) -> None:
    _program(light, 3)
    await _push(hass, light)
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT, effect="Demo")
    await _read_back(hass, light)
    assert light.events == [("demo",), ("query_status",)]


async def test_unknown_effect_is_rejected(hass, config_entry, light) -> None:
    light.clear()
    with pytest.raises(ServiceValidationError):
        await _call(hass, "light", "turn_on", LIGHT, effect="Aurora")
    assert light.events == []


async def test_turn_off_sends_only_power_and_keeps_the_look(hass, config_entry, light) -> None:
    light.clear()
    await _call(hass, "light", "turn_off", LIGHT)
    await _read_back(hass, light)
    assert light.events == [("power", False), ("query_status",)]
    assert hass.states.get(LIGHT).state == "off"
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT)
    await _read_back(hass, light)
    assert light.events == [("power", True), ("query_status",)]
    attrs = hass.states.get(LIGHT).attributes
    assert attrs["brightness"] == 230
    assert tuple(attrs["rgbw_color"]) == (0, 255, 0, 0)


@pytest.mark.parametrize(("option", "index"), [(o, i) for i, o in enumerate(PROGRAM_OPTIONS)])
async def test_select_program_never_touches_power(hass, config_entry, light, option, index) -> None:
    await _push(hass, light, power=False)
    light.clear()
    await _call(hass, "select", "select_option", PROGRAM, option=option)
    await _read_back(hass, light)
    assert light.events == [("program", index), ("query_status",)]
    assert hass.states.get(PROGRAM).state == option
    assert hass.states.get(MODE).state == "program"
    assert hass.states.get(LIGHT).state == "off"


@pytest.mark.parametrize("running", ["scene", "program"])
async def test_select_manual_restores_the_register_colour(hass, config_entry, light, running) -> None:
    light.rgbw = [20, 40, 60, 80]
    _scene(light, 2) if running == "scene" else _program(light, 4)
    await _push(hass, light)
    light.clear()
    await _call(hass, "select", "select_option", PROGRAM, option="manual")
    await _read_back(hass, light)
    assert light.events == [("white", 80, 3), ("color", 20, 40, 60), ("query_status",)]
    assert hass.states.get(PROGRAM).state == "manual"
    assert tuple(hass.states.get(LIGHT).attributes["rgbw_color"]) == (51, 102, 153, 204)


async def test_release_link_button_releases_then_resumes(hass, config_entry, light) -> None:
    await _call(hass, "button", "press", RELEASE)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert not light.connected
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_RESUME_AFTER - 10))
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED  # not before the promised time
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_RESUME_AFTER + 5))
    await wait_until(lambda: config_entry.state is ConfigEntryState.LOADED, message="the entry to resume")
    await wait_until(lambda: hass.states.get(LIGHT).state == "on", message="the light after the resume")
    assert light.connected


async def test_sync_time_button_writes_the_local_time(hass, config_entry, light) -> None:
    er.async_get(hass).async_update_entity(SYNC_TIME, disabled_by=None)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))  # HA reloads the entry a moment later
    await wait_until(lambda: hass.states.get(SYNC_TIME) is not None, message="the enabled button")
    await wait_until(lambda: hass.states.get(LIGHT).state == "on", message="the reloaded entry")
    # 13:14:15 UTC on Wednesday 4 March 2026 is 05:14:15 PST in the test time zone.
    now = dt_util.as_local(datetime(2026, 3, 4, 13, 14, 15, tzinfo=UTC))
    light.clear()
    with patch.object(dt_util, "now", return_value=now):
        await _call(hass, "button", "press", SYNC_TIME)
    await _read_back(hass, light)
    assert light.events == [("time_sync", 5, 14, 15, 3), ("query_status",)]


# ---------------------------------------------------------------------------------------------
# commands that follow each other before the light has been heard from
# ---------------------------------------------------------------------------------------------


async def test_turning_on_right_after_turning_off_powers_the_light_on_again(hass, setup_entry, light, monkeypatch):
    """The read-back of the off command has not arrived yet, so the cached state still says "on". The on
    command must not trust it: it used to skip the power frame and leave the light off."""
    monkeypatch.setattr(coordinator_module, "STATUS_DEBOUNCE", 30.0)  # the read-back comes long after both commands
    await setup_entry()
    light.clear()

    await _call(hass, "light", "turn_off", LIGHT)
    assert hass.states.get(LIGHT).state == "on"  # nothing was heard back yet: the state only follows the light
    await _call(hass, "light", "turn_on", LIGHT, brightness=255)

    assert light.events == [("power", False), ("power", True), ("brightness", 100)]
    assert light.power is True


async def test_a_turn_on_queued_behind_a_turn_off_still_powers_the_light_on(hass, setup_entry, light, monkeypatch):
    """The power frame is decided when the command gets its turn, after the commands queued before it."""
    monkeypatch.setattr(coordinator_module, "STATUS_DEBOUNCE", 30.0)
    coordinator = (await setup_entry()).runtime_data
    light.clear()

    async with coordinator.link.transaction():  # something else owns the link: both commands have to wait
        off = asyncio.create_task(coordinator.async_turn_off())
        on = asyncio.create_task(coordinator.async_turn_on())
        for _ in range(20):
            await asyncio.sleep(0)
    await asyncio.gather(off, on)

    assert light.events == [("power", False), ("power", True)]
    assert light.power is True


async def test_a_confirmed_state_stops_the_extra_power_frame(hass, config_entry, light) -> None:
    """Once the light has answered, turning on an already lit light sends no power frame again."""
    light.clear()
    await _call(hass, "light", "turn_off", LIGHT)
    await _read_back(hass, light)
    assert hass.states.get(LIGHT).state == "off"
    light.clear()
    await _call(hass, "light", "turn_on", LIGHT)
    await _read_back(hass, light)
    assert hass.states.get(LIGHT).state == "on"
    light.clear()

    await _call(hass, "light", "turn_on", LIGHT, brightness=128)
    await _read_back(hass, light)

    assert light.events == [("brightness", 50), ("query_status",)]
