"""State, commands and lifecycle of one Pawfly light.

The coordinator is push-driven: the link parses every notification of the light and hands
each ``Status`` to ``_on_status``, which is the only writer of ``coordinator.data``. Nothing
here assumes a command took effect; after every command a debounced ``query_status`` asks
the light what it actually did. The scheduled poll is only a heartbeat for the case where
nothing was heard for a while.

Two process-level pieces deliberately live outside this object because a coordinator is
rebuilt on every reload and every ``ConfigEntryNotReady`` retry: the outage clock and its
repair (``outage.py``) and the link supervisor's own timers (owned by the link).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import outage, protocol
from .const import (
    CONF_KEEP_CONNECTED,
    CONF_LAST_HOLDING_PROXY,
    CONF_PENDING_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_PREFERRED_PROXY,
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EFFECT_DEMO,
    TIME_SYNC_HOUR,
    TIME_SYNC_MINUTE,
)
from .link import (
    ON_DEMAND_REQUEST_WINDOW,
    AuthFailed,
    ChangeUnconfirmed,
    CommandFailed,
    LinkError,
    LinkEvent,
    NotConnected,
    PawflyLink,
    Transaction,
)
from .model import CHANNELS, RGBW, is_diy

_LOGGER = logging.getLogger(__name__)

type PawflyConfigEntry = ConfigEntry[PawflyCoordinator]

#: Wait after the last command before asking the light for its status.
STATUS_DEBOUNCE = 0.2
#: How long setup waits for the first session before raising ``ConfigEntryNotReady``.
FIRST_SESSION_TIMEOUT = 20.0
#: How long the status query after a command may wait for the link.
STATUS_LINK_TIMEOUT = 5.0
#: How long a live preview call waits for the link (previews are best effort).
PREVIEW_LINK_TIMEOUT = 3.0
#: A preview that is not refreshed for this long is ended on the light automatically.
PREVIEW_IDLE_END = 30.0
#: Settle time between the last program point and its read-back.
PROGRAM_SETTLE = 0.25
#: Bound for the best-effort "end preview" during shutdown.
SHUTDOWN_PREVIEW_END_TIMEOUT = 3.0


@dataclass(frozen=True)
class ProgramReading:
    """A program as returned by ``get_program``/``set_program``."""

    program: int
    source: str  # "device" (read back over Bluetooth) or "preset" (the app's built-in curve)
    points: tuple[protocol.Point, ...]


class PawflyCoordinator(DataUpdateCoordinator[protocol.Status | None]):
    """Owns the link to one light and everything derived from it."""

    config_entry: PawflyConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PawflyConfigEntry) -> None:
        options = entry.options
        self.keep_connected: bool = options.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED)
        poll = int(options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=poll),
            always_update=False,
        )
        self.address: str = str(entry.data[CONF_ADDRESS]).upper()
        self.link = PawflyLink(
            hass,
            self.address,
            name=entry.title,
            password=entry.data[CONF_PASSWORD],
            pending_password=entry.data.get(CONF_PENDING_PASSWORD),
            hold=self.keep_connected,
            preferred_proxy=lambda: entry.options.get(CONF_PREFERRED_PROXY) or None,
            on_status=self._on_status,
            on_event=self._on_link_event,
            on_route=self._on_route,
            on_password=self._store_password,
        )
        self._status_debouncer: Debouncer[None] = Debouncer(
            hass,
            _LOGGER,
            cooldown=STATUS_DEBOUNCE,
            immediate=False,
            function=self._async_status_after_command,
        )
        self._unsub_time_sync: CALLBACK_TYPE | None = None
        self._closing = False
        self._setup_complete = False
        # A power-off was written and no status has been heard since: the cached "on" is not to be trusted.
        self._power_unconfirmed = False

        self._preview_pending: tuple[RGBW | None] | None = None
        self._preview_writing = False
        self._preview_idle = asyncio.Event()
        self._preview_idle.set()
        self._preview_active = False
        self._preview_unsub: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------------ state ---------

    @property
    def link_healthy(self) -> bool:
        """Whether the link is in a state that needs no repair (live, not cached)."""
        if self.link.ready:
            return True
        return not self.keep_connected and not self.link.failing and not self.link.auth_failed

    @property
    def available(self) -> bool:
        """Whether entities should show the last status as current.

        With a held link that means the link is up. An on-demand link is released on
        purpose after idling, so the last known status stays visible until a connection
        attempt actually fails.
        """
        if self.data is None or self.link.auth_failed:
            return False
        if self.keep_connected:
            return self.link.ready
        return self.link.ready or not self.link.failing

    # ------------------------------------------------------------------ lifecycle ------

    async def async_start(self) -> None:
        """Start the link and wait (bounded) for the first session.

        Raises ``ConfigEntryAuthFailed`` when the light refuses the password and
        ``ConfigEntryNotReady`` when it cannot be reached in time; in both cases the link is
        stopped again before the exception propagates, and an unreachable light arms the
        process-scoped outage clock.
        """
        self.link.start()
        outcome = await self.link.async_wait_first_session(FIRST_SESSION_TIMEOUT)
        if outcome == "ready":
            self._unsub_time_sync = async_track_time_change(
                self.hass,
                self._async_daily_time_sync,
                hour=TIME_SYNC_HOUR,
                minute=TIME_SYNC_MINUTE,
                second=0,
            )
            self._setup_complete = True
            return
        error = self.link.last_error or "no answer"
        await self.async_shutdown()
        if outcome == "auth_failed":
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"name": self.config_entry.title},
            )
        outage.async_link_lost(self.hass, self.config_entry)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="not_ready",
            translation_placeholders={"name": self.config_entry.title, "error": error},
        )

    async def async_shutdown(self) -> None:
        """Release the light and stop everything; bounded and idempotent.

        Used by unload and by the shutdown job. The latch comes first: from here on nothing
        (supervisor, backoff, advertisement, poll, command) opens a connection or uses the link.
        """
        if self._closing:
            return
        self._closing = True
        self.link.latch()
        if self._unsub_time_sync is not None:
            self._unsub_time_sync()
            self._unsub_time_sync = None
        self._cancel_preview_watchdog()
        if self._preview_active and self.link.ready:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(SHUTDOWN_PREVIEW_END_TIMEOUT):
                    async with self.link.transaction(timeout=SHUTDOWN_PREVIEW_END_TIMEOUT) as tx:
                        await tx.write(protocol.preview(None))
            self._preview_active = False
        self._status_debouncer.async_shutdown()
        await self.link.async_stop()
        await super().async_shutdown()

    # ------------------------------------------------------------------ callbacks ------

    @callback
    def _on_status(self, status: protocol.Status) -> None:
        """A status arrived from the light: the one place ``data`` is written."""
        if self._closing:
            return
        self._power_unconfirmed = False  # the light has spoken again: its answer is the truth
        if status != self.data:
            self.async_set_updated_data(status)

    @callback
    def _store_password(self, password: str | None) -> None:
        """Keep the entry in step with the key the link settled on; ``None``: no key works.

        Any key that was pending is dropped either way (settled, or refused together with the old one).
        """
        entry = self.config_entry
        data = {key: value for key, value in entry.data.items() if key != CONF_PENDING_PASSWORD}
        if password is not None:
            data[CONF_PASSWORD] = password
        if data != dict(entry.data):
            self.hass.config_entries.async_update_entry(entry, data=data)

    @callback
    def _on_route(self, _source: str | None, adapter: str | None) -> None:
        """Remember which proxy carries the link (its ESPHome node name, never a MAC string)."""
        entry = self.config_entry
        if self._closing or not adapter or entry.data.get(CONF_LAST_HOLDING_PROXY) == adapter:
            return
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_LAST_HOLDING_PROXY: adapter})

    @callback
    def _on_link_event(self, event: LinkEvent) -> None:
        if self._closing:
            return
        if event is LinkEvent.READY:
            outage.async_link_recovered(self.hass, self.address)
        elif event in (LinkEvent.DROPPED, LinkEvent.FAILED):
            outage.async_link_lost(self.hass, self.config_entry)
        elif event is LinkEvent.AUTH_FAILED:
            self._store_password(None)  # neither the saved nor a pending key opens the light
            if self._setup_complete:
                self.config_entry.async_start_reauth(self.hass)
        self.async_update_listeners()

    # ------------------------------------------------------------------ polling ---------

    async def _async_update_data(self) -> protocol.Status | None:
        """Heartbeat: ask for the status. A held link is never reconnected from here.

        Failures are not update errors: whether entities are available is decided by the
        link state, so a poll that cannot reach the light keeps the last status.
        """
        if self._closing:
            return self.data
        try:
            if self.keep_connected:
                if not self.link.ready:
                    return self.data
                return await self.link.async_query_status(timeout=STATUS_LINK_TIMEOUT)
            async with self.link.transaction(timeout=ON_DEMAND_REQUEST_WINDOW) as tx:
                return await tx.query_status()
        except LinkError as err:
            _LOGGER.debug("%s: status poll failed: %s", self.config_entry.title, err)
            return self.data

    async def _async_status_after_command(self) -> None:
        """Debounced follow-up to a command: read back what the light really did."""
        if self._closing:
            return
        try:
            await self.link.async_query_status(timeout=STATUS_LINK_TIMEOUT)
        except LinkError as err:
            _LOGGER.debug("%s: status after command failed: %s", self.config_entry.title, err)

    @callback
    def _schedule_status(self) -> None:
        self._status_debouncer.async_schedule_call()

    async def _async_daily_time_sync(self, _now: datetime) -> None:
        try:
            await self.async_sync_time()
        except HomeAssistantError as err:
            # Not fatal: every reconnect syncs the clock again.
            _LOGGER.debug("%s: scheduled time sync skipped: %s", self.config_entry.title, err)

    # ------------------------------------------------------------------ commands --------

    @contextlib.asynccontextmanager
    async def _tx(self, *, timeout: float | None = None) -> AsyncIterator[Transaction]:
        """``link.transaction()`` with link errors turned into translated HA errors."""
        name = self.config_entry.title
        if self._closing:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_connected",
                translation_placeholders={"name": name, "error": "Home Assistant is shutting down"},
            )
        try:
            if timeout is None:
                async with self.link.transaction() as tx:
                    yield tx
            else:
                async with self.link.transaction(timeout=timeout) as tx:
                    yield tx
        except AuthFailed as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed", translation_placeholders={"name": name}
            ) from err
        except NotConnected as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_connected",
                translation_placeholders={"name": name, "error": str(err)},
            ) from err
        except CommandFailed as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"name": name, "error": str(err)},
            ) from err

    async def _write(self, *frames: bytes) -> None:
        """Write frames as one transaction and schedule the status read-back."""
        async with self._tx() as tx:
            await tx.write(*frames)
        self._schedule_status()

    async def async_turn_on(
        self,
        *,
        brightness: int | None = None,
        rgbw: RGBW | None = None,
        effect: str | None = None,
    ) -> None:
        """Power on, then apply master brightness (1-100), an effect or a manual colour.

        ``effect`` wins over ``rgbw``; a colour puts the light in manual mode (the white
        command from a scenario or program flips the mode, verified live).
        """
        status = self.data
        if effect == EFFECT_DEMO:
            if status is None or status.mode is not protocol.Mode.PROGRAM:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="demo_needs_program",
                    translation_placeholders={"name": self.config_entry.title},
                )
        elif effect is not None and effect not in protocol.SCENARIO_NAMES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_effect",
                translation_placeholders={"effect": effect},
            )
        async with self._tx() as tx:
            # Decided here, at this command's turn on the link and not when it was issued: a power-off
            # queued ahead of it (or written a moment ago and not yet read back) changes what "already
            # on" means, and the cached status cannot know.
            frames: list[bytes] = []
            current = self.data
            if current is None or not current.power or self._power_unconfirmed:
                frames.append(protocol.power(True))
            if brightness is not None:
                frames.append(protocol.brightness(brightness))
            if effect == EFFECT_DEMO:
                frames.append(protocol.demo())
            elif effect is not None:
                frames.append(protocol.scenario(protocol.SCENARIO_NAMES.index(effect)))
            elif rgbw is not None:
                red, green, blue, white = rgbw
                frames.append(protocol.white(white))
                frames.append(protocol.color(red, green, blue))
            if frames:
                await tx.write(*frames)
        self._schedule_status()

    async def async_turn_off(self) -> None:
        async with self._tx() as tx:
            # Inside the lock no answer to an earlier query of ours can still be on its way, so the next
            # status is the light's answer to this write. Until then a cached "on" must not be believed.
            self._power_unconfirmed = True
            await tx.write(protocol.power(False))
        self._schedule_status()

    async def async_activate_manual(self) -> None:
        """Manual mode with the last manual colour (the status frame keeps that register)."""
        status = self.data
        if status is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_connected",
                translation_placeholders={"name": self.config_entry.title, "error": "no status yet"},
            )
        await self._write(
            protocol.white(status.white),
            protocol.color(status.red, status.green, status.blue),
        )

    async def async_activate_program(self, program: int) -> None:
        """Run a stored program (0-2 built-in styles, 3-5 DIY 1-3)."""
        await self._write(protocol.program(program))

    async def async_set_channels(self, levels: dict[str, int]) -> None:
        """Set only the given channels (percent), leaving the others as they are.

        One frame per channel (opcode 0x04 with the channel byte); like any colour write this
        puts the light in manual mode, and it does not turn the light on.
        """
        await self._write(*(protocol.channel(CHANNELS.index(name), levels[name]) for name in CHANNELS if name in levels))

    async def async_sync_time(self) -> None:
        """Push the local time to the light (which has no clock of its own after a power loss)."""
        await self._write(protocol.time_sync(dt_util.now()))

    async def async_rename(self, name: str) -> None:
        """Rename the light (display name without the ``PY4C`` prefix, as the app does)."""
        try:
            frame = protocol.rename(name)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_name",
                translation_placeholders={"error": str(err)},
            ) from err
        async with self._tx() as tx:
            await tx.write(frame)

    async def async_change_password(self, new_password: str) -> None:
        """Change the light's key and, once the light proved it accepts it, the stored one."""
        try:
            protocol.change_key(new_password)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_password",
                translation_placeholders={"error": str(err)},
            ) from err
        name = self.config_entry.title
        try:
            await self.link.async_change_password(new_password)
        except ChangeUnconfirmed as err:
            # The light may already want the new key: keep it, so a reload or restart before the light
            # is heard from again still tries it first (and settles it) instead of losing it.
            if self.config_entry.data.get(CONF_PASSWORD) != new_password:  # not already settled a moment ago
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data={**self.config_entry.data, CONF_PENDING_PASSWORD: new_password}
                )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="password_change_unconfirmed",
                translation_placeholders={"name": name},
            ) from err
        except AuthFailed as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed", translation_placeholders={"name": name}
            ) from err
        except NotConnected as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_connected",
                translation_placeholders={"name": name, "error": str(err)},
            ) from err
        except CommandFailed as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="password_not_accepted", translation_placeholders={"name": name}
            ) from err
        self._store_password(new_password)  # the link already did when its session opened; this is the guarantee

    # ------------------------------------------------------------------ programs --------

    async def async_get_program(self, program: int) -> ProgramReading:
        """A program's points. The built-in styles 0-2 are the app's own curves: the light does
        not answer a query for them, so they are served without any Bluetooth traffic."""
        if not is_diy(program):
            return ProgramReading(program, "preset", tuple(protocol.PRESETS[program]))
        async with self._tx() as tx:
            points = await tx.read_program(program)
        return ProgramReading(program, "device", tuple(points))

    async def async_write_program(self, program: int, points: Sequence[protocol.Point]) -> ProgramReading:
        """Replace DIY ``program`` (3-5), read it back and verify the light stored it as sent."""
        try:
            frames = protocol.program_upload(program, points)
        except (ValueError, TypeError) as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_points",
                translation_placeholders={"error": str(err)},
            ) from err
        expected = sorted(points, key=lambda p: (p.hour, p.minute))
        async with self._tx() as tx:
            # The light acknowledges nothing and drops frames that come too fast, so the only
            # proof of a stored program is reading it back. One retry covers a lost frame.
            for attempt in (1, 2):
                await tx.write(*frames)
                await asyncio.sleep(PROGRAM_SETTLE)
                stored = await tx.read_program(program)
                if stored == expected:
                    break
                _LOGGER.warning(
                    "%s: %s read back %s of %s points%s",
                    self.config_entry.title,
                    protocol.PROGRAM_NAMES[program],
                    len(stored),
                    len(expected),
                    "; uploading it once more" if attempt == 1 else "",
                )
            else:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="program_readback_mismatch",
                    translation_placeholders={
                        "name": self.config_entry.title,
                        "program": protocol.PROGRAM_NAMES[program],
                        "expected": str(len(expected)),
                        "found": str(len(stored)),
                    },
                )
            active = self.data
            if active is not None and active.mode is protocol.Mode.PROGRAM and active.program == program:
                # The running program picks up the new points when it is selected again.
                await tx.write(protocol.program(program))
        self._schedule_status()
        return ProgramReading(program, "device", tuple(stored))

    # ------------------------------------------------------------------ preview ---------

    async def async_preview(self, rgbw: RGBW | None) -> None:
        """Show a colour on the light without changing its mode; ``None`` ends the preview.

        Latest wins: calls that arrive while a write is in flight only replace the pending
        colour, so a dragged slider can call this as fast as it likes. Ending a preview
        returns only after an end frame was written, or raises: when the write it waited for
        failed, the caller writes the end frame itself. A preview nobody refreshes for
        ``PREVIEW_IDLE_END`` seconds is ended on the light automatically.
        """
        self._preview_pending = (rgbw,)
        while self._preview_writing:
            if rgbw is not None:
                return  # the writer in flight picks the newest colour up
            await self._preview_idle.wait()
            if self._preview_pending is None and not self._preview_writing:
                return  # the writer got through everything, this end request included
        await self._async_write_previews()

    async def _async_write_previews(self) -> None:
        """Write the pending request, newest wins, until none is left. One caller writes at a time."""
        self._preview_writing = True
        self._preview_idle.clear()
        try:
            while self._preview_pending is not None:
                (target,) = self._preview_pending
                self._preview_pending = None
                try:
                    async with self._tx(timeout=PREVIEW_LINK_TIMEOUT) as tx:
                        await tx.write(protocol.preview(target))
                except BaseException:
                    if self._preview_pending is None:
                        # Not delivered: leave it for an end request that queued behind this write.
                        self._preview_pending = (target,)
                    raise
                self._preview_active = target is not None
                self._arm_preview_watchdog()
        finally:
            self._preview_writing = False
            self._preview_idle.set()

    @callback
    def _arm_preview_watchdog(self) -> None:
        self._cancel_preview_watchdog()
        if self._preview_active and not self._closing:
            self._preview_unsub = async_call_later(self.hass, PREVIEW_IDLE_END, self._async_preview_watchdog)

    @callback
    def _cancel_preview_watchdog(self) -> None:
        unsub, self._preview_unsub = self._preview_unsub, None
        if unsub is not None:
            unsub()

    async def _async_preview_watchdog(self, _now: datetime) -> None:
        self._preview_unsub = None
        _LOGGER.debug("%s: ending a preview nobody refreshed", self.config_entry.title)
        try:
            await self.async_preview(None)
        except HomeAssistantError as err:
            _LOGGER.debug("%s: could not end the preview: %s", self.config_entry.title, err)

    # ------------------------------------------------------------------ diagnostics -----

    def diagnostics(self) -> dict[str, Any]:
        """Plain-data view for the diagnostics download."""
        status = self.data
        return {
            "keep_connected": self.keep_connected,
            "available": self.available,
            "link": self.link.snapshot(),
            "status": None
            if status is None
            else {
                "power": status.power,
                "brightness": status.brightness,
                "speed": status.speed,
                "mode": status.mode.name,
                "scenario": status.scenario,
                "program": status.program,
                "red": status.red,
                "green": status.green,
                "blue": status.blue,
                "white": status.white,
            },
            "preview_active": self._preview_active,
        }
