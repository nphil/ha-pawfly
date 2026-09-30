"""The connection manager in a real Home Assistant: session start-up, reconnects, outages, shutdown.

Only the light is fake. The link supervisor, the coordinator, the entities, the outage clock and
its repair, ``release_link`` and Home Assistant's own timers and config-entry states are real.

Time is faked in two places, each only where a test needs it. Wall-clock behaviour (what the light's
clock is set to, the daily 03:17 sync) runs under ``freezer``: a frozen clock never wakes
``asyncio.sleep``, so those tests turn the event loop by hand (``spin``/``run_for``). The 15-minute
outage runs on a fake ``outage.monotonic`` plus ``async_fire_time_changed`` for Home Assistant's timers.
Everything else uses the harness's real but shrunken timing constants.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bleak.exc import BleakError
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD, EVENT_HOMEASSISTANT_STOP, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.pawfly import coordinator as coordinator_module
from custom_components.pawfly import link as link_module
from custom_components.pawfly import outage as outage_module
from custom_components.pawfly import protocol
from custom_components.pawfly import release as release_module
from custom_components.pawfly.const import (
    CONF_KEEP_CONNECTED,
    CONF_LAST_HOLDING_PROXY,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

from .conftest import BASE, ENTRY_TITLE, wait_until
from .fake_light import DEFAULT_KEY, PROXY_ADAPTER, FakeGattClient, FakePawflyLight

LIGHT = f"light.{BASE}"
SELECT = f"select.{BASE}_program"
CONNECTED = f"binary_sensor.{BASE}_connected"
ISSUE_ID = "aabbcc112233_unreachable"
ON_DEMAND = {CONF_KEEP_CONNECTED: False}
SESSION = ["start_notify", "verify_key", "time_sync", "query_status"]
# The documented reconnect policy, read at import: the autouse fixture shrinks it for the suite.
PRODUCTION_BACKOFF = link_module.RECONNECT_BACKOFF
PRODUCTION_JITTER = link_module.RECONNECT_BACKOFF_JITTER


# -- helpers -----------------------------------------------------------------------------------


def link_tasks() -> list[asyncio.Task]:
    return [task for task in asyncio.all_tasks() if task.get_name().startswith("pawfly link") and not task.done()]


async def settle(turns: int = 40) -> None:
    """Let every ready callback and task run (the fake light answers without waiting)."""
    for _ in range(turns):
        await asyncio.sleep(0)


def names(events: list[tuple]) -> list[str]:
    return [event[0] for event in events]


async def fire(hass: HomeAssistant, seconds: float = DEFAULT_POLL_INTERVAL + 2) -> None:
    """Fire the timers due within ``seconds`` of now (by default: the next poll) and let their work finish."""
    before = asyncio.all_tasks()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    # A setup retry or a poll is a background task that block_till_done does not wait for. The link
    # supervisor a retry starts is meant to outlive it.
    if spawned := asyncio.all_tasks() - before - set(link_tasks()):
        await asyncio.wait(spawned, timeout=10)
    await hass.async_block_till_done()


def issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)


def outages(hass: HomeAssistant) -> dict:
    return hass.data[DOMAIN]["outages"]


def at(freezer, wall_clock: str) -> None:
    """Move the frozen clock to a wall-clock time in Home Assistant's own time zone."""
    freezer.move_to(datetime.fromisoformat(wall_clock).replace(tzinfo=dt_util.get_default_time_zone()))


async def spin(freezer, predicate: Callable[[], bool], message: str, step: float = 0.01) -> None:
    """``wait_until`` for a frozen clock: each turn of the loop moves time a little, timers run on it."""
    for _ in range(600):
        if predicate():
            return
        freezer.tick(step)
        await asyncio.sleep(0)
    raise AssertionError(f"timed out waiting for {message}")


async def run_for(freezer, seconds: float, step: float = 0.01) -> None:
    for _ in range(round(seconds / step)):
        freezer.tick(step)
        await asyncio.sleep(0)


@pytest.fixture
async def pacific(hass: HomeAssistant) -> None:
    """The UTC-versus-local and daylight-saving cases below are US ones: pin the zone they rely on."""
    await hass.config.async_set_time_zone("US/Pacific")


# -- session start-up and the daily clock ----------------------------------------------------------


@pytest.mark.parametrize(
    ("wall_clock", "expected"),
    [
        ("2026-01-04 21:05:09", (21, 5, 9, 7)),  # Sunday evening: it is already Monday in UTC
        ("2026-01-05 00:00:01", (0, 0, 1, 1)),  # Monday, a second after local midnight
        ("2026-01-10 23:59:58", (23, 59, 58, 6)),  # Saturday
    ],
)
async def test_every_session_starts_key_time_status_with_local_time(
    pacific, freezer, setup_entry, light, wall_clock, expected
):
    at(freezer, wall_clock)
    await setup_entry()
    startup = [("start_notify",), ("verify_key", DEFAULT_KEY), ("time_sync", *expected), ("query_status",)]
    assert light.events == startup

    light.clear()
    light.drop_link()
    await spin(freezer, lambda: light.events_named("query_status"), "the second session")
    assert [event for event in light.events if event != ("disconnect",)] == startup


async def test_a_command_issued_during_startup_is_written_after_the_status_query(config_entry, light):
    coordinator = config_entry.runtime_data
    light.answer_key = False  # the key check goes unanswered, so start-up idles until KEY_REPLY_TIMEOUT
    light.clear()
    light.drop_link()
    await wait_until(lambda: light.events_named("verify_key"), message="the new session's key check")
    assert not coordinator.link.ready and not light.events_named("time_sync")

    # An unavailable entity ignores service calls, so this is the coordinator call the entities make.
    await coordinator.async_turn_on(brightness=50)  # device percent; queued behind the handshake

    events = [event for event in light.events if event != ("disconnect",)]
    assert names(events)[:5] == [*SESSION, "brightness"]
    assert events[4] == ("brightness", 50)


async def test_daily_time_sync_fires_once_at_0317_local_and_again_next_day_across_dst(
    hass, pacific, freezer, setup_entry, light
):
    at(freezer, "2026-03-07 03:10:00")  # Saturday, standard time; daylight saving starts next morning
    await setup_entry()
    light.clear()

    async def syncs_after_moving_to(wall_clock: str) -> list[tuple]:
        at(freezer, wall_clock)
        async_fire_time_changed(hass)
        await settle()
        return light.events_named("time_sync")

    saturday = ("time_sync", 3, 17, 0, 6)
    assert await syncs_after_moving_to("2026-03-07 03:16:00") == []
    assert await syncs_after_moving_to("2026-03-07 03:17:00") == [saturday]
    assert await syncs_after_moving_to("2026-03-07 03:18:00") == [saturday]
    assert await syncs_after_moving_to("2026-03-08 03:17:00") == [saturday, ("time_sync", 3, 17, 0, 7)]


async def test_daily_sync_with_the_link_down_writes_nothing_and_the_next_reconnect_syncs(
    hass, pacific, freezer, setup_entry, light
):
    at(freezer, "2026-03-07 03:10:00")
    link = (await setup_entry()).runtime_data.link
    light.connect_error = BleakError("out of range")
    light.drop_link()
    light.clear()
    await spin(freezer, lambda: link.failing, "the link to be failing")

    at(freezer, "2026-03-07 03:17:00")
    before = asyncio.all_tasks()
    async_fire_time_changed(hass)
    fired = asyncio.all_tasks() - before
    assert fired  # the sync is waiting for the link
    await run_for(freezer, 2.0)  # longer than COMMAND_LINK_TIMEOUT: the sync gives up
    assert light.events_named("time_sync") == []
    assert all(task.done() and task.exception() is None for task in fired)

    light.connect_error = None
    await spin(freezer, lambda: link.ready, "the reconnect")
    assert [event[:3] for event in light.events_named("time_sync")] == [("time_sync", 3, 17)]


# -- one held link, no races ----------------------------------------------------------------------


async def test_poll_asks_for_the_status_on_the_held_link_and_never_reconnects(hass, config_entry, light):
    light.clear()
    await fire(hass)
    await wait_until(lambda: light.events, message="the poll's status query")
    await settle()
    assert light.events == [("query_status",)]
    assert light.connections == 1


async def test_poll_while_the_link_is_down_does_not_connect_and_keeps_the_last_status(
    hass, config_entry, light, monkeypatch
):
    coordinator = config_entry.runtime_data
    monkeypatch.setattr(link_module, "RECONNECT_BACKOFF", (3600.0,))  # park the supervisor in a long backoff
    light.connect_error = BleakError("out of range")
    light.drop_link()
    await wait_until(lambda: coordinator.link.failing, message="the link to fail")
    status, attempts = coordinator.data, coordinator.link.connect_attempts
    light.connect_error = None  # a poll that dialled the light would succeed now
    light.clear()

    with patch.object(coordinator, "_async_update_data", wraps=coordinator._async_update_data) as polled:
        await fire(hass)
        await settle()
    assert polled.await_count == 1
    assert (light.events, light.connections, coordinator.link.connect_attempts) == ([], 1, attempts)
    assert status is not None and coordinator.data == status
    assert hass.states.get(LIGHT).state == STATE_UNAVAILABLE


async def test_a_change_made_on_the_light_itself_shows_up_after_the_next_poll(hass, config_entry, light):
    light.brightness = 10  # the light never pushes this
    await settle()
    assert hass.states.get(LIGHT).attributes["brightness"] == 230  # still the 90 % last heard: nothing is assumed
    await fire(hass)
    await wait_until(lambda: hass.states.get(LIGHT).attributes["brightness"] == 26, message="the polled brightness")


async def test_simultaneous_commands_never_interleave_their_frames(hass, config_entry, light, monkeypatch):
    real_write = FakeGattClient.write_gatt_char

    async def slow_write(self, char, data, response=None, **kwargs):
        await asyncio.sleep(0.005)  # a real GATT write takes time; without the lock a command could slip in
        await real_write(self, char, data, response, **kwargs)

    monkeypatch.setattr(FakeGattClient, "write_gatt_char", slow_write)
    light.clear()
    await asyncio.gather(
        hass.services.async_call(
            "light", "turn_on", {"entity_id": LIGHT, "rgbw_color": [255, 0, 0, 255]}, blocking=True
        ),
        hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": "diy_2"}, blocking=True),
    )
    colour = [("white", 100, 3), ("color", 100, 0, 0)]
    assert light.events_named("white", "color", "program") in (colour + [("program", 4)], [("program", 4)] + colour)


# -- reconnect and backoff ---------------------------------------------------------------------------


def test_reconnect_delay_follows_the_documented_schedule_with_jitter(monkeypatch):
    monkeypatch.setattr(link_module, "RECONNECT_BACKOFF", PRODUCTION_BACKOFF)
    monkeypatch.setattr(link_module, "RECONNECT_BACKOFF_JITTER", PRODUCTION_JITTER)
    steady = lambda low, high: 0.0  # noqa: E731
    assert [link_module.reconnect_delay(failures, steady) for failures in range(9)] == [0, 1, 2, 5, 10, 30, 60, 60, 60]
    bounds: list[tuple[float, float]] = []

    def top(low: float, high: float) -> float:
        bounds.append((low, high))
        return high

    assert link_module.reconnect_delay(3, top) == pytest.approx(6.0)  # 5 s plus 20 %
    assert bounds == [(-0.2, 0.2)]
    assert link_module.reconnect_delay(3, lambda low, high: low) == pytest.approx(4.0)  # 5 s minus 20 %
    assert all(4.0 <= link_module.reconnect_delay(3) <= 6.0 for _ in range(200))


@pytest.mark.parametrize(
    "error", [BleakError, OSError, TimeoutError, RuntimeError], ids=["bleak", "os", "timeout", "bug"]
)
async def test_connect_errors_keep_the_supervisor_retrying_until_the_light_returns(hass, config_entry, light, error):
    link = config_entry.runtime_data.link
    light.connect_error = error("no route")
    light.drop_link()
    await wait_until(lambda: link.failures >= 3, message="three failed attempts")

    snapshot = link.snapshot()
    assert (snapshot["ready"], snapshot["drops_1h"]) == (False, 1)
    assert snapshot["last_error"] == f"{error.__name__}: no route"
    assert hass.states.get(LIGHT).state == STATE_UNAVAILABLE
    assert hass.states.get(CONNECTED).state == "off"
    assert hass.states.get(CONNECTED).attributes["reconnect_attempt"] >= 3

    light.connect_error = None
    await wait_until(lambda: hass.states.get(LIGHT).state == "on", message="the light to be available again")
    snapshot = link.snapshot()
    assert {key: snapshot[key] for key in ("consecutive_failures", "last_error", "drops_1h", "sessions")} == {
        "consecutive_failures": 0,
        "last_error": None,
        "drops_1h": 1,
        "sessions": 2,
    }


@pytest.mark.parametrize(("min_gap", "reconnects"), [(0.0, True), (3600.0, False)], ids=["heard", "rate_limited"])
async def test_an_advertisement_while_failing_cuts_the_backoff_short_unless_rate_limited(
    config_entry, light, monkeypatch, min_gap, reconnects
):
    link = config_entry.runtime_data.link
    monkeypatch.setattr(link_module, "RECONNECT_BACKOFF", (3600.0,))
    monkeypatch.setattr(link_module, "ADVERT_KICK_MIN_GAP", min_gap)
    light.connect_error = BleakError("out of range")
    light.drop_link()
    await wait_until(lambda: link.failing, message="the link to fail")
    attempts = link.connect_attempts
    light.connect_error = None
    light.fire_advertisement()
    if reconnects:
        await wait_until(lambda: link.ready, message="the reconnect the advertisement asked for")
    else:
        await settle()
        assert not link.ready and link.connect_attempts == attempts


async def test_an_advertisement_heard_during_a_failing_attempt_does_not_skip_the_next_backoff(
    config_entry, light, monkeypatch
):
    """A kick only means something while the supervisor waits in a backoff. One raised during an attempt
    used to stay pending and swallow the backoff that follows the attempt's failure."""
    link = config_entry.runtime_data.link
    monkeypatch.setattr(link_module, "RECONNECT_BACKOFF", (0.6,))
    light.connect_error = BleakError("out of range")
    light.connect_delay = 0.25  # every attempt takes a moment to fail
    light.drop_link()
    await wait_until(lambda: link.failures == 1, message="the first failed attempt")
    await wait_until(lambda: link.connect_attempts == 3, message="the attempt after the first backoff to start")

    light.fire_advertisement()  # heard while that attempt is still in flight
    await wait_until(lambda: link.failures == 2, message="that attempt to fail")
    await asyncio.sleep(0.2)  # well inside the 0.6 s backoff it has to serve
    assert link.connect_attempts == 3 and link.state == "backoff"

    await wait_until(lambda: link.connect_attempts == 4, message="the retry once the backoff is over")
    light.connect_error, light.connect_delay = None, 0.0


async def test_a_command_while_down_cuts_the_backoff_short(config_entry, light, monkeypatch):
    coordinator = config_entry.runtime_data
    monkeypatch.setattr(link_module, "RECONNECT_BACKOFF", (3600.0,))
    light.connect_error = BleakError("out of range")
    light.drop_link()
    await wait_until(lambda: coordinator.link.failing, message="the link to fail")
    light.connect_error = None
    light.clear()

    # The entity is unavailable while down and would ignore a service call: use the call it makes.
    await coordinator.async_turn_off()  # waits for the reconnect it kicks, then writes

    assert names(light.events)[:5] == [*SESSION, "power"]
    assert light.events[4] == ("power", False)
    assert light.connections == 2


# -- dead links -------------------------------------------------------------------------------------------


async def test_unanswered_status_queries_get_a_wedged_link_dropped_and_rebuilt(config_entry, light, monkeypatch):
    monkeypatch.setattr(link_module, "STATUS_REPLY_TIMEOUT", 0.1)
    coordinator = config_entry.runtime_data
    light.silent = True
    for _ in range(link_module.STATUS_MISS_LIMIT - 1):
        await coordinator.async_refresh()  # each poll goes unanswered
    assert (light.connections, light.disconnections) == (1, 0)  # a few misses do not cost the link

    await coordinator.async_refresh()
    await wait_until(lambda: light.disconnections == 1, message="the wedged link to be dropped")
    light.silent = False
    await wait_until(lambda: coordinator.link.ready and light.connections == 2, message="a new session")


async def test_a_light_that_never_answers_the_first_session_leaves_setup_retrying_with_no_link_left(
    hass, light, make_entry, monkeypatch
):
    monkeypatch.setattr(coordinator_module, "FIRST_SESSION_TIMEOUT", 0.6)
    light.silent = True
    entry = make_entry()
    async with asyncio.timeout(coordinator_module.FIRST_SESSION_TIMEOUT + 2.0):
        await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert light.disconnections >= 1 and not light.connected
    assert link_tasks() == []


# -- first setup outcomes --------------------------------------------------------------------------------


@pytest.mark.parametrize("problem", ["invisible", "refusing"])
async def test_an_unreachable_light_ends_setup_in_retry(hass, light, make_entry, monkeypatch, problem):
    monkeypatch.setattr(coordinator_module, "FIRST_SESSION_TIMEOUT", 0.3)
    if problem == "invisible":
        light.visible = False
    else:
        light.connect_error = BleakError("connection refused")
    entry = make_entry()
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert light.connections == 0 and link_tasks() == []


async def test_a_wrong_key_fails_setup_and_starts_reauth(hass, light, make_entry):
    entry = make_entry(password="87654321")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    assert light.events == [("start_notify",), ("verify_key", "87654321"), ("stop_notify",), ("disconnect",)]
    assert link_tasks() == []


# -- outage clock and repair -----------------------------------------------------------------------------


@pytest.fixture
def outage_clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stands in for ``outage.monotonic``: time moves only when a test moves it."""
    clock = SimpleNamespace(now=5_000.0, elapsed=0.0)

    def advance(seconds: float) -> None:
        clock.now += seconds
        clock.elapsed += seconds

    clock.advance = advance
    monkeypatch.setattr(outage_module, "monotonic", lambda: clock.now)
    return clock


@pytest.fixture
def setup_attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """One item per setup attempt: every retry and every reload builds a new coordinator."""
    attempts: list[str] = []
    real_start = coordinator_module.PawflyCoordinator.async_start

    async def counting(self) -> None:
        attempts.append(self.config_entry.entry_id)
        await real_start(self)

    monkeypatch.setattr(coordinator_module.PawflyCoordinator, "async_start", counting)
    return attempts


async def elapse(hass: HomeAssistant, clock: SimpleNamespace, seconds: float) -> None:
    """Let time pass for the outage clock and for Home Assistant's timers (setup retries, the deadline)."""
    clock.advance(seconds)
    await fire(hass, clock.elapsed)


@pytest.fixture
def setup_unreachable(hass, light, make_entry, monkeypatch, outage_clock, setup_attempts):
    """Factory: set an entry up while its light is out of reach (SETUP_RETRY at T0, outage clock running)."""
    monkeypatch.setattr(coordinator_module, "FIRST_SESSION_TIMEOUT", 0.3)

    async def _setup(*, heard: bool = False, proxy: str | None = None) -> MockConfigEntry:
        if heard:
            light.connect_error = BleakError("connection refused")  # advertising, but it will not connect
        else:
            light.visible = False
        data = {CONF_ADDRESS: light.address.lower()}  # any case: the clock is keyed by the upper-case address
        if proxy:
            data[CONF_LAST_HOLDING_PROXY] = proxy
        entry = make_entry(data=data)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY
        return entry

    return _setup


@pytest.mark.parametrize(
    ("heard", "proxy"), [(False, None), (True, PROXY_ADAPTER)], ids=["out_of_range", "heard_but_refusing"]
)
async def test_retries_never_restart_the_outage_clock_and_the_repair_comes_after_fifteen_minutes(
    hass, light, setup_unreachable, outage_clock, setup_attempts, heard, proxy
):
    await setup_unreachable(heard=heard, proxy=proxy)
    assert list(outages(hass)) == [light.address]
    assert outage_module.outage_seconds(hass, light.address) == 0

    await elapse(hass, outage_clock, 300)  # the retry five minutes in finds the light still down
    assert len(setup_attempts) == 2
    assert outage_module.outage_seconds(hass, light.address) == 300
    assert issue(hass) is None

    await elapse(hass, outage_clock, 599)
    assert issue(hass) is None
    await elapse(hass, outage_clock, 1)
    repair = issue(hass)
    assert (repair.is_fixable, repair.severity) == (False, ir.IssueSeverity.WARNING)
    assert repair.translation_key == "unreachable"
    assert repair.translation_placeholders == {"name": ENTRY_TITLE, "minutes": "15", "proxy": proxy or "unknown"}


async def test_a_manual_reload_mid_outage_does_not_restart_the_clock(
    hass, light, setup_unreachable, outage_clock, setup_attempts
):
    entry = await setup_unreachable()
    outage_clock.advance(300)
    await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY and len(setup_attempts) == 2
    assert outage_module.outage_seconds(hass, light.address) == 300


async def test_the_deadline_timer_alone_raises_the_repair(hass, setup_unreachable, outage_clock):
    entry = await setup_unreachable()
    entry.async_cancel_retry_setup()  # no setup retry can report the outage: only the deadline timer is left
    await elapse(hass, outage_clock, 900)
    assert issue(hass) is not None


async def test_recovery_clears_the_clock_and_deletes_the_repair(hass, light, setup_unreachable, outage_clock):
    entry = await setup_unreachable()
    await elapse(hass, outage_clock, 900)
    assert issue(hass) is not None

    light.visible = True
    await elapse(hass, outage_clock, 1)  # the next setup retry finds the light
    assert entry.state is ConfigEntryState.LOADED
    assert issue(hass) is None and outages(hass) == {}
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("seconds", [300, 900], ids=["before_the_repair", "after_the_repair"])
async def test_removing_the_entry_mid_outage_forgets_the_clock_and_the_repair(
    hass, setup_unreachable, outage_clock, seconds
):
    entry = await setup_unreachable()
    await elapse(hass, outage_clock, seconds)
    assert (issue(hass) is not None) == (seconds == 900)

    await hass.config_entries.async_remove(entry.entry_id)
    assert outages(hass) == {} and issue(hass) is None


async def test_a_disabled_entry_is_not_reported_as_unreachable(hass, setup_unreachable, outage_clock):
    entry = await setup_unreachable()
    await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await elapse(hass, outage_clock, 900)
    assert issue(hass) is None and outages(hass) == {}


async def test_a_link_lost_after_setup_raises_the_repair_and_the_next_session_deletes_it(
    hass, config_entry, light, outage_clock
):
    light.connect_error = BleakError("out of range")
    light.drop_link()
    await wait_until(lambda: hass.data[DOMAIN].get("outages"), message="the outage clock to start")
    await elapse(hass, outage_clock, 900)
    repair = issue(hass)
    assert repair is not None and repair.translation_placeholders["proxy"] == PROXY_ADAPTER  # where it was held last

    light.connect_error = None
    await wait_until(lambda: issue(hass) is None, message="the repair to clear on the next session")
    assert outages(hass) == {}


# -- keep_connected off ---------------------------------------------------------------------------------


async def wait_released(light: FakePawflyLight) -> None:
    await wait_until(lambda: ("disconnect",) in light.events, timeout=5, message="the idle link to be released")


async def test_on_demand_link_reads_a_status_then_is_released_but_entities_stay_available(hass, setup_entry, light):
    await setup_entry(options=ON_DEMAND)
    assert names(light.events) == SESSION  # setup still reads a first status
    await wait_released(light)
    assert names(light.events)[4:] == ["stop_notify", "disconnect"]
    await wait_until(lambda: hass.states.get(CONNECTED).state == "off", message="the connected sensor to go off")
    state = hass.states.get(LIGHT)
    assert state.state == "on" and state.attributes["brightness"] == 230  # the last status, not "unavailable"
    assert outage_module.outage_seconds(hass, light.address) is None  # an idle link is not an outage


async def test_on_demand_command_connects_again_with_the_full_startup(hass, setup_entry, light):
    await setup_entry(options=ON_DEMAND)
    await wait_released(light)
    light.clear()
    await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT, "brightness": 128}, blocking=True)
    assert names(light.events)[:5] == [*SESSION, "brightness"]
    assert light.events[1] == ("verify_key", DEFAULT_KEY) and light.events[4] == ("brightness", 50)


async def test_a_failed_on_demand_attempt_makes_entities_unavailable_until_a_poll_connects(
    hass, setup_entry, light, monkeypatch
):
    monkeypatch.setattr(link_module, "COMMAND_LINK_TIMEOUT", 0.4)
    entry = await setup_entry(options=ON_DEMAND)
    await wait_released(light)
    light.connect_error = BleakError("out of range")
    with pytest.raises(HomeAssistantError) as error:
        await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT, "brightness": 128}, blocking=True)
    assert error.value.translation_key == "not_connected"
    assert hass.states.get(LIGHT).state == STATE_UNAVAILABLE

    light.connect_error = None
    await entry.runtime_data.async_refresh()  # the next poll dials the light again
    assert hass.states.get(LIGHT).state == "on"


async def test_the_idle_release_never_cuts_a_transaction_in_flight(hass, setup_entry, light, monkeypatch):
    """A DIY upload is a header, its points and a read-back: about two seconds at the real frame gap.
    With keep_connected off the link is released after ON_DEMAND_LINGER of silence; that deadline must
    not fire while the command still owns the link (it used to tear the link down after the header)."""
    monkeypatch.setattr(link_module, "FRAME_GAP", 0.15)  # 13 frames and the read-back take far longer than 0.6 s
    entry = await setup_entry(options=ON_DEMAND)
    await wait_released(light)
    light.clear()

    points = [protocol.Point(hour, 0, hour * 4, 0, 0, 0) for hour in range(1, 13)]
    reading = await entry.runtime_data.async_write_program(3, points)

    assert (reading.source, len(reading.points)) == ("device", 12)
    assert [point.hour for point in light.programs[3]] == list(range(1, 13))
    wire = names(light.events)
    assert "disconnect" not in wire[: wire.index("query_program") + 1]  # the link held until the read-back came
    await wait_released(light)  # the deadline was renewed, not cancelled: the link is still let go afterwards


# -- shutdown ---------------------------------------------------------------------------------------------


async def test_unload_releases_the_link_within_a_bounded_time(hass, config_entry, light):
    light.clear()
    async with asyncio.timeout(link_module.STOP_GRACE):
        assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert light.events == [("stop_notify",), ("disconnect",)] and light.disconnections == 1
    assert link_tasks() == []


async def test_unload_stays_bounded_when_the_disconnect_hangs(hass, config_entry, light, monkeypatch):
    async def hang(self, **_):
        await asyncio.sleep(3600)

    monkeypatch.setattr(FakeGattClient, "disconnect", hang)
    monkeypatch.setattr(link_module, "DISCONNECT_DEADLINE", 0.2)
    async with asyncio.timeout(1.0):  # far below STOP_GRACE: the disconnect deadline ends the wait
        assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED and link_tasks() == []


async def test_home_assistant_stopping_drops_the_link_while_the_entry_stays_loaded(hass, config_entry, light):
    light.clear()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert light.events == [("stop_notify",), ("disconnect",)] and light.disconnections == 1
    assert config_entry.state is ConfigEntryState.LOADED and link_tasks() == []


# -- release_link -----------------------------------------------------------------------------------------


async def release(hass: HomeAssistant, **data: int) -> None:
    await hass.services.async_call(DOMAIN, "release_link", data, blocking=True)


def spy_on_setup(hass: HomeAssistant):
    return patch.object(hass.config_entries, "async_setup", wraps=hass.config_entries.async_setup)


async def test_release_link_drops_the_link_now_and_resumes_after_the_default_delay(hass, config_entry, light):
    light.clear()
    await release(hass)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert light.events == [("stop_notify",), ("disconnect",)]
    await fire(hass, 170)
    assert config_entry.state is ConfigEntryState.NOT_LOADED  # the documented default is 180 s
    await fire(hass, 190)
    assert config_entry.state is ConfigEntryState.LOADED and light.connections == 2


async def _unload_refused(entry_id: str) -> bool:
    return False


async def _unload_raises(entry_id: str) -> bool:
    raise RuntimeError("unload exploded")


async def _unload_hangs(entry_id: str) -> bool:
    await asyncio.sleep(3600)
    return True


@pytest.mark.parametrize("fake_unload", [_unload_refused, _unload_raises, _unload_hangs])
async def test_a_release_that_did_not_unload_is_reported_naming_the_entry(hass, config_entry, monkeypatch, fake_unload):
    monkeypatch.setattr(release_module, "RELEASE_DEADLINE", 0.2)
    with patch.object(hass.config_entries, "async_unload", fake_unload), pytest.raises(HomeAssistantError) as error:
        await release(hass)
    assert error.value.translation_domain == DOMAIN and error.value.translation_key == "release_failed"
    assert error.value.translation_placeholders == {"names": ENTRY_TITLE}
    assert config_entry.state is ConfigEntryState.LOADED


@pytest.fixture
def second_light(light: FakePawflyLight):
    """A second fake light on its own address: connects and advertisements are routed by address."""
    reef = FakePawflyLight(address="AA:BB:CC:44:55:66", name="PY4C-BT-REEF")
    lights = {light.address: light, reef.address: reef}

    async def establish(client_class, device, name, disconnected_callback=None, **kwargs):
        return lights[device.address.upper()].new_client(disconnected_callback)

    def device_from_address(hass, address, connectable=True):
        return lights[address.upper()].device() if address.upper() in lights else None

    def register(hass, callback, matcher, mode):
        target, registration = lights[matcher["address"].upper()], (callback, dict(matcher))
        target.advert_callbacks.append(registration)
        return lambda: target.advert_callbacks.remove(registration) if registration in target.advert_callbacks else None

    with (
        patch("custom_components.pawfly.link.establish_connection", side_effect=establish),
        patch("custom_components.pawfly.link.bluetooth.async_ble_device_from_address", side_effect=device_from_address),
        patch("custom_components.pawfly.link.bluetooth.async_register_callback", side_effect=register),
    ):
        yield reef


async def test_a_failed_release_still_resumes_the_lights_that_were_released(hass, config_entry, second_light):
    reef = MockConfigEntry(
        domain=DOMAIN,
        title="Reef Tank Light",
        unique_id=second_light.address,
        data={CONF_ADDRESS: second_light.address, CONF_PASSWORD: DEFAULT_KEY},
    )
    reef.add_to_hass(hass)
    await hass.config_entries.async_setup(reef.entry_id)
    await hass.async_block_till_done()
    assert reef.state is ConfigEntryState.LOADED
    real_unload = hass.config_entries.async_unload

    async def unload(entry_id: str) -> bool:
        return False if entry_id == config_entry.entry_id else await real_unload(entry_id)

    with patch.object(hass.config_entries, "async_unload", unload), pytest.raises(HomeAssistantError) as error:
        await release(hass, resume_after=60)
    assert error.value.translation_placeholders == {"names": ENTRY_TITLE}
    assert (config_entry.state, reef.state) == (ConfigEntryState.LOADED, ConfigEntryState.NOT_LOADED)

    await fire(hass, 65)
    assert reef.state is ConfigEntryState.LOADED and second_light.connections == 2
    assert await hass.config_entries.async_unload(reef.entry_id)


@pytest.mark.parametrize("change", ["disabled", "removed", "set_up_by_hand"])
async def test_only_the_same_still_enabled_entry_is_resumed(hass, config_entry, light, change):
    await release(hass, resume_after=60)
    if change == "disabled":
        await hass.config_entries.async_set_disabled_by(config_entry.entry_id, ConfigEntryDisabler.USER)
    elif change == "removed":
        await hass.config_entries.async_remove(config_entry.entry_id)
    else:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    connections = light.connections

    with spy_on_setup(hass) as setup:
        await fire(hass, 65)
    setup.assert_not_called()
    assert light.connections == connections


async def test_removing_the_entry_cancels_its_pending_resume(hass, config_entry):
    await release(hass, resume_after=60)
    assert list(hass.data[DOMAIN]["resume"]) == [config_entry.entry_id]
    await hass.config_entries.async_remove(config_entry.entry_id)
    assert hass.data[DOMAIN]["resume"] == {}


async def test_resume_after_zero_keeps_the_light_released(hass, config_entry):
    await release(hass, resume_after=0)
    with spy_on_setup(hass) as setup:
        await fire(hass, 3600)
    setup.assert_not_called()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_a_later_release_without_a_delay_cancels_the_earlier_resume(hass, config_entry):
    """The newest request decides, even though the light is already released when it arrives."""
    await release(hass, resume_after=60)
    await release(hass, resume_after=0)
    with spy_on_setup(hass) as setup:
        await fire(hass, 3600)
    setup.assert_not_called()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_a_later_release_with_a_new_delay_replaces_the_earlier_resume(hass, config_entry):
    await release(hass, resume_after=60)
    await release(hass, resume_after=600)
    await fire(hass, 65)
    assert config_entry.state is ConfigEntryState.NOT_LOADED  # the 60 s timer no longer exists
    await fire(hass, 610)
    assert config_entry.state is ConfigEntryState.LOADED


async def test_a_stale_resume_does_not_bring_back_an_entry_that_was_set_up_and_unloaded_by_hand(hass, config_entry):
    """Setting the entry up independently ends the pending resume: the timer must not act on a later
    unload that the user chose."""
    await release(hass, resume_after=60)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    with spy_on_setup(hass) as setup:
        await fire(hass, 65)
    setup.assert_not_called()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_a_pending_resume_does_not_survive_home_assistant_stopping(hass, config_entry):
    await release(hass, resume_after=60)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    with spy_on_setup(hass) as setup:
        await fire(hass, 65)
    setup.assert_not_called()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_releasing_a_light_that_is_not_connected_is_harmless(hass, setup_unreachable):
    entry = await setup_unreachable()
    await release(hass)  # nothing is held, so nothing is unloaded and nothing is scheduled
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.data[DOMAIN].get("resume", {}) == {}


# -- an unloaded entry is not an outage ------------------------------------------------------------------


async def test_an_entry_unloaded_mid_outage_is_not_reported_but_keeps_the_clock_origin(
    hass, light, setup_unreachable, outage_clock
):
    """Unloaded (a release, a reload in flight) is not a link outage to report, yet the clock is not
    reset by it: when the entry is set up again and the light is still down, the repair follows at once."""
    entry = await setup_unreachable()
    await elapse(hass, outage_clock, 300)
    assert await hass.config_entries.async_unload(entry.entry_id)

    await elapse(hass, outage_clock, 900)  # 20 minutes since the first drop, but nothing is set up
    assert issue(hass) is None
    assert outage_module.outage_seconds(hass, light.address) == 1200

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert issue(hass) is not None


async def test_release_link_forgives_the_outage_it_interrupts(hass, config_entry, light, outage_clock):
    """Handing the light over on purpose must not count towards 'unreachable'."""
    light.connect_error = BleakError("out of range")
    light.drop_link()
    await wait_until(lambda: hass.data[DOMAIN].get("outages"), message="the outage clock to start")
    await elapse(hass, outage_clock, 600)

    await release(hass, resume_after=0)

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert outages(hass) == {}
    await elapse(hass, outage_clock, 900)
    assert issue(hass) is None
