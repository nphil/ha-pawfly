"""Persistent Bluetooth link to one Pawfly light (the connection manager).

The light accepts ONE Bluetooth connection at a time and speaks through a single GATT
characteristic (``ffe1``): commands are written to it, replies arrive as notifications.
Set commands (family ``0xAD``) are never answered; only ``0xBD`` queries reply, so the
state of the light is always learned by asking (``query_status``) and by parsing every
notification, never assumed from what was written.

Ownership and ordering (the house BLE rules, patterned on fluvalble's held link):

* ONE supervisor task owns the whole GATT lifecycle: connect through
  ``bleak_retry_connector.establish_connection`` (routed by ``ble_affinity`` when a preferred
  proxy is configured), run the session start-up, hold the link, tear it down, back off.
  Nothing else connects or disconnects, so polling and commands can never race the hold loop.
* One ``asyncio.Lock`` serialises every GATT write. Session start-up runs inside it, so a
  command can never interleave with the key/time/status handshake.
* Session start-up order is fixed: subscribe to notifications, verify the key, sync the
  clock, query the status. The link is "ready" only after the first status arrived.
* Every GATT-facing await has a hard deadline (bleak and the ESPHome proxy can wait forever
  on a wedged link) and teardown is bounded.
* Reconnects back off 1, 2, 5, 10, 30, 60 s (+-20 % jitter) after failures; a drop of an
  established link retries after one second. An advertisement heard while failing shortens
  the wait, rate limited.
* ``keep_connected`` False turns the same machinery into an on-demand link: it connects
  when a command or poll asks for it and releases the light after an idle period. The idle
  release never cuts a command in flight: it waits for the transaction and counts from its end.
* A retry is requested (a command, an advertisement) only counts while the supervisor is
  actually waiting in a backoff; one raised during an attempt belongs to that attempt.
* Two keys can be in play after ``async_change_password``: the new one is tried first (the
  light may already want it) and the previous one only if the light explicitly refuses it.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
import contextlib
from datetime import datetime
from enum import StrEnum
import logging
import random
import time
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
import bleak_retry_connector
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from . import protocol
from .ble_affinity import make_affinity_client_class

_LOGGER = logging.getLogger(__name__)

# --- deadlines: every GATT-facing await is bounded -------------------------------------
CONNECT_DEADLINE = 30.0
CONNECT_RETRIES = 3
GATT_OP_DEADLINE = 10.0
STOP_NOTIFY_DEADLINE = 2.0
DISCONNECT_DEADLINE = 5.0
KEY_REPLY_TIMEOUT = 3.0
STATUS_REPLY_TIMEOUT = 3.0
PROGRAM_REPLY_TIMEOUT = 5.0
#: How long a command waits for the (re)connecting link before it gives up.
COMMAND_LINK_TIMEOUT = 12.0
#: Graceful part of ``async_stop``; a supervisor still busy after it is cancelled.
STOP_GRACE = 7.0
STOP_CANCEL_WAIT = 3.0
#: How long ``async_change_password`` waits for the light to accept the new key on a new link:
#: the whole worst case of one reconnect (teardown, connect, start-up), so a slow reconnect that
#: still works is not mistaken for a refusal.
RELINK_TIMEOUT = CONNECT_DEADLINE + KEY_REPLY_TIMEOUT + STATUS_REPLY_TIMEOUT + DISCONNECT_DEADLINE + STOP_NOTIFY_DEADLINE

# --- pacing ----------------------------------------------------------------------------
#: Minimum pause between two GATT writes. The light silently DROPS frames that arrive faster
#: (measured live: a 12-point upload at 1 ms lost 4 points, at 10 ms lost 1, at 15 ms was exact
#: only half the time; 20-100 ms were always exact), so every write, not just uploads, waits.
FRAME_GAP = max(0.1, protocol.FRAME_GAP_S)
#: Settle time after ``change_key`` before the link is dropped to prove the new key.
CHANGE_KEY_SETTLE = 0.3

# --- reconnect policy ------------------------------------------------------------------
RECONNECT_BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
RECONNECT_BACKOFF_JITTER = 0.2
#: Pause after an established link dropped (a drop itself is not a failure).
DROP_RETRY_DELAY = 1.0
#: One WARNING per this many consecutive failures instead of one per attempt.
RECONNECT_WARN_EVERY = 10
#: A command may cut the backoff short at most this often.
KICK_MIN_GAP = 5.0
#: Advertisements shorten the backoff at most this often.
ADVERT_KICK_MIN_GAP = 15.0
DROP_WINDOW_SECONDS = 3600.0
#: Consecutive unanswered status queries on a "ready" link before it is presumed wedged.
STATUS_MISS_LIMIT = 3

# --- on-demand mode (keep_connected False) ---------------------------------------------
#: Idle time after the last exchange before an on-demand link is released.
ON_DEMAND_LINGER = 30.0
#: How long a request keeps the supervisor trying to connect.
ON_DEMAND_REQUEST_WINDOW = COMMAND_LINK_TIMEOUT + 3.0


class LinkError(Exception):
    """Base class of the errors this module raises."""


class NotConnected(LinkError):
    """The light is not connected (and did not become so in time)."""


class AuthFailed(LinkError):
    """The light refused the password."""


class CommandFailed(LinkError):
    """A write or query failed or was not answered."""


class ChangeUnconfirmed(LinkError):
    """A new password was written but no session could be opened with it in time.

    The light may already want the new key, so the link keeps it pending and settles it on the
    next connect (``on_password`` reports the outcome).
    """


class LinkEvent(StrEnum):
    """What happened to the link (delivered to the ``on_event`` callback)."""

    READY = "ready"  # a session was established: key accepted, clock set, status read
    DROPPED = "dropped"  # an established link went away without being asked to
    FAILED = "failed"  # a connect or start-up attempt failed
    IDLE = "idle"  # an on-demand link was released after its idle time
    RELINK = "relink"  # the link was dropped on purpose to open a fresh session (a password change)
    AUTH_FAILED = "auth_failed"  # the light refused the password


def _carries_key(frame: bytes) -> bool:
    """Key check and key change frames contain the password (packed): never log them."""
    return len(frame) > 2 and (frame[0], frame[2]) in (
        (protocol.FAMILY_QUERY, protocol.QUERY_VERIFY_KEY),
        (protocol.FAMILY_SET, protocol.CMD_CHANGE_KEY),
    )


def needs_write_response(char: Any) -> bool:
    """Write kind for ``char``: unacknowledged when it supports it, else acknowledged.

    Unacknowledged is the verified path on the real light through an ESPHome proxy (the
    light never answers set commands at the protocol level either); the frame gap, not an
    ATT acknowledgement, is what keeps its firmware from dropping frames.
    """
    return "write-without-response" not in set(char.properties)


def _seconds_iso(moment: datetime | None) -> str | None:
    """ISO time at whole-second precision (state attributes must not churn on microseconds)."""
    return None if moment is None else moment.replace(microsecond=0).isoformat()


def reconnect_delay(failures: int, jitter: Callable[[float, float], float] = random.uniform) -> float:
    """Backoff before the next attempt after ``failures`` consecutive failures."""
    if failures <= 0:
        return 0.0
    base = RECONNECT_BACKOFF[min(failures, len(RECONNECT_BACKOFF)) - 1]
    return max(0.0, base * (1.0 + jitter(-RECONNECT_BACKOFF_JITTER, RECONNECT_BACKOFF_JITTER)))


def decode(data: bytes) -> list[Any]:
    """All messages in one notification (it may carry several frames back to back)."""
    raw = bytes(data)
    messages = [msg for frame in protocol.split_frames(raw) if (msg := protocol.parse(frame)) is not None]
    if not messages and (msg := protocol.parse(raw)) is not None:
        messages.append(msg)
    return messages


class Transaction:
    """Exclusive use of the ready link; only valid inside ``PawflyLink.transaction()``.

    The GATT lock is held for the whole ``async with`` block, so a multi-step command (a
    program upload followed by its read-back, a colour change followed by the status query)
    can never interleave with another command or with the poll.
    """

    def __init__(self, link: PawflyLink, client: BleakClient) -> None:
        self._link = link
        self._client = client

    async def write(self, *frames: bytes) -> None:
        """Write frames in order (no reply is expected for set commands)."""
        for frame in frames:
            await self._link._write_frame(self._client, frame)  # noqa: SLF001

    async def query_status(self, timeout: float | None = None) -> protocol.Status:
        """Ask for the status and wait for the reply (any status notification counts)."""
        return await self._link._query_status(  # noqa: SLF001
            self._client, STATUS_REPLY_TIMEOUT if timeout is None else timeout
        )

    async def read_program(self, program: int, timeout: float | None = None) -> list[protocol.Point]:
        """Read a stored program (3-5 = DIY 1-3) back from the light."""
        return await self._link._read_program(  # noqa: SLF001
            self._client, program, PROGRAM_REPLY_TIMEOUT if timeout is None else timeout
        )


class PawflyLink:
    """The held (or on-demand) Bluetooth link to one light."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        *,
        name: str,
        password: str,
        pending_password: str | None = None,
        hold: bool,
        preferred_proxy: Callable[[], str | None],
        on_status: Callable[[protocol.Status], None],
        on_event: Callable[[LinkEvent], None],
        on_route: Callable[[str | None, str | None], None] | None = None,
        on_password: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] = dt_util.now,
    ) -> None:
        self.hass = hass
        self.address = address.upper()
        self.name = name
        # The key the next session starts with. A pending key (written to the light, not yet proved)
        # goes first; the previous key is the fallback if the light explicitly refuses it.
        self._password = password
        self._fallback_password: str | None = None
        self._key_unresolved = pending_password is not None
        if pending_password is not None and pending_password != password:
            self._password, self._fallback_password = pending_password, password
        self._hold = hold
        self._preferred_proxy = preferred_proxy
        self._on_status = on_status
        self._on_event = on_event
        self._on_route = on_route
        self._on_password = on_password
        self._clock = clock

        self._client: BleakClient | None = None
        self._client_class: type | None = None
        self._char: Any = None
        self._write_response = True
        self._notifying = False
        self._last_device: BLEDevice | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._unsub_advert: CALLBACK_TYPE | None = None

        self._stopping = False
        self._latched = False
        self._ready = False
        self._auth_failed = False
        self._disconnected = asyncio.Event()
        self._wake = asyncio.Event()
        self._kick_requested = False
        self._backing_off = False
        self._busy = 0  # transactions that own the GATT lock or are waiting for it
        self._drop_requested: str | None = None
        self._drop_intentional = False
        self._want_until = 0.0
        self._linger_until = 0.0
        self._last_write = 0.0
        self._last_attempt_start = 0.0
        self._last_attempt_end = 0.0
        self._status_misses = 0
        self._ready_waiters: list[asyncio.Future[None]] = []

        self._status_waiters: list[asyncio.Future[protocol.Status]] = []
        self._key_waiter: asyncio.Future[protocol.KeyResult] | None = None
        self._program_waiters: dict[int, asyncio.Future[list[protocol.Point]]] = {}
        self._assembler = protocol.ProgramAssembler()

        # accounting shown in diagnostics
        self.status: protocol.Status | None = None
        self.session_count = 0
        self.connect_attempts = 0
        self.failures = 0
        self.last_error: str | None = None
        self.last_drop: datetime | None = None
        self.connected_since: datetime | None = None
        self.route_source: str | None = None
        self.route_adapter: str | None = None
        self.via_preferred_proxy: bool | None = None
        self.last_notification: str | None = None
        self.last_notification_at: datetime | None = None
        self._drops: deque[float] = deque()

    # ------------------------------------------------------------------ public state ---

    @property
    def ready(self) -> bool:
        """A session is established (key accepted, clock set, status read)."""
        return self._ready

    @property
    def hold(self) -> bool:
        """Whether the link is held permanently (``keep_connected``)."""
        return self._hold

    @property
    def failing(self) -> bool:
        """The latest connect/start-up attempts failed and no session is up."""
        return self.failures > 0 and not self._ready

    @property
    def auth_failed(self) -> bool:
        """The light refused the password; the supervisor waits for new credentials."""
        return self._auth_failed

    @property
    def state(self) -> str:
        """Coarse state for diagnostics."""
        if self._stopping:
            return "stopped"
        if self._auth_failed:
            return "auth_failed"
        if self._ready:
            return "ready"
        if self._task is None:
            return "not_started"
        if self._client is not None:
            return "starting"
        if not self._wanted():
            return "idle"
        return "backoff" if self._backing_off else "connecting"

    def drops_in_window(self) -> int:
        """Unexpected drops during the last hour."""
        cutoff = time.monotonic() - DROP_WINDOW_SECONDS
        while self._drops and self._drops[0] < cutoff:
            self._drops.popleft()
        return len(self._drops)

    def snapshot(self) -> dict[str, Any]:
        """Plain-data view of the link for diagnostics and the connected sensor."""
        return {
            "state": self.state,
            "ready": self._ready,
            "hold": self._hold,
            "auth_failed": self._auth_failed,
            "password_pending": self._key_unresolved,
            "connect_attempts": self.connect_attempts,
            "consecutive_failures": self.failures,
            "sessions": self.session_count,
            "drops_1h": self.drops_in_window(),
            "last_drop": _seconds_iso(self.last_drop),
            "last_error": self.last_error,
            "connected_since": _seconds_iso(self.connected_since),
            "route_source": self.route_source,
            "route_adapter": self.route_adapter,
            "preferred_proxy": self._preferred_proxy() or "",
            "via_preferred_proxy": self.via_preferred_proxy,
            "last_notification": self.last_notification,
            "last_notification_at": _seconds_iso(self.last_notification_at),
        }

    # ------------------------------------------------------------------ lifecycle -----

    def start(self) -> None:
        """Start the supervisor (idempotent)."""
        if self._task is not None:
            return
        self._stopping = False
        self._unsub_advert = bluetooth.async_register_callback(
            self.hass,
            self._on_advertisement,
            {"address": self.address, "connectable": True},
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
        self._task = self.hass.async_create_background_task(
            self._supervise(), f"pawfly link {self.address}"
        )

    def latch(self) -> None:
        """Never open another connection from now on (the process is shutting down).

        Unlike ``async_stop`` this leaves a live session alone, so a last command (ending a
        preview) can still use it; it is permanent, ``start`` does not clear it.
        """
        self._latched = True
        self._wake.set()

    async def async_stop(self) -> None:
        """Release the light and stop the supervisor; bounded (about 10 s at worst)."""
        self._stopping = True
        self._wake.set()
        if self._unsub_advert is not None:
            self._unsub_advert()
            self._unsub_advert = None
        self._resolve_ready_waiters(NotConnected("the link is stopping"))
        self._fail_waiters(NotConnected("the link is stopping"))
        task, self._task = self._task, None
        if task is not None:
            # No cancel yet: the supervisor's own teardown disconnects cleanly, and a second
            # cancellation in the middle of it could leave a ghost link on the light.
            _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE)
            if pending:
                _LOGGER.warning("%s: link supervisor did not stop in %ss; cancelling it", self.name, STOP_GRACE)
                task.cancel()
                await asyncio.wait({task}, timeout=STOP_CANCEL_WAIT)
        self._set_ready(False)

    # ------------------------------------------------------------------ requests --------

    def request_link(self, linger: float | None = None) -> None:
        """On-demand mode: ask the supervisor to bring the link up for a while."""
        if self._hold:
            return
        window = ON_DEMAND_REQUEST_WINDOW if linger is None else linger
        self._want_until = max(self._want_until, time.monotonic() + window)
        self._wake.set()

    def kick(self, min_gap: float | None = None) -> None:
        """End a reconnect backoff early (a user command wants the light now).

        Only a supervisor that is waiting in a backoff can be kicked. A request raised while it is
        attempting to connect would stay pending and swallow the backoff after that attempt fails.
        """
        if self._ready or self._stopping or self._auth_failed or not self._backing_off:
            return
        if time.monotonic() - self._last_attempt_start < (KICK_MIN_GAP if min_gap is None else min_gap):
            return
        self._kick_requested = True
        self._wake.set()

    def request_drop(self, reason: str, *, intentional: bool = False) -> None:
        """Ask the supervisor to tear the current link down and reconnect."""
        if self._client is None:
            return
        self._drop_requested = reason
        self._drop_intentional = intentional
        self._wake.set()

    @contextlib.asynccontextmanager
    async def transaction(self, *, timeout: float | None = None) -> AsyncIterator[Transaction]:
        """Exclusive use of the ready link for a multi-step command.

        Waits up to ``timeout`` seconds for the link (kicking the supervisor and, in
        on-demand mode, asking it to connect), then holds the GATT lock for the block.
        """
        await self._wait_ready(COMMAND_LINK_TIMEOUT if timeout is None else timeout)
        # Counted from here until the block ends, also while waiting for the lock: an on-demand
        # link is never released under a command, however long it takes.
        self._busy += 1
        try:
            async with self._lock:
                client = self._client
                if not self._ready or client is None:
                    raise NotConnected("the link dropped while the command waited for its turn")
                self._touch()  # the idle deadline counts from the moment the command owns the link ...
                try:
                    yield Transaction(self, client)
                finally:
                    self._touch()  # ... and again from its end
        finally:
            self._busy -= 1
            if not self._hold:
                self._wake.set()  # a release postponed because of this command can be decided now

    async def async_query_status(self, timeout: float | None = None) -> protocol.Status:
        """Query the status on the ready link (used by polls and after commands)."""
        async with self.transaction(timeout=timeout) as tx:
            return await tx.query_status()

    async def async_wait_first_session(self, timeout: float) -> str:
        """Wait for the first session: ``ready``, ``auth_failed`` or ``timeout``."""
        self.request_link()
        try:
            await self._wait_ready(timeout, kick=False)
        except AuthFailed:
            return "auth_failed"
        except NotConnected:
            return "timeout"
        return "ready"

    async def async_change_password(self, new_password: str) -> None:
        """Write a new key and prove it by opening a fresh session with it.

        The light never acknowledges the write and answers a second key check on an
        authenticated link unreliably, so the only proof is a new session: the link is dropped
        and re-established. From the moment the frame is sent the light may want the new key
        whatever happens next, so the next session tries it first and the previous key is used
        only if the light explicitly refuses it (``8A 00``):

        * a session opens with the new key: done (``on_password`` reports it);
        * the light refuses the new key and takes the previous one: ``CommandFailed``;
        * it refuses both: ``AuthFailed`` (the link parks and asks for reauth);
        * no session opens within the whole reconnect budget: ``ChangeUnconfirmed``. The new key
          stays pending (the supervisor keeps trying it first) and is settled on the next connect.
        """
        frame = protocol.change_key(new_password)  # ValueError for a malformed key
        async with self.transaction() as tx:
            # Set before the write, inside the lock: a write that fails after the frame went out
            # must not leave the link holding a key the light no longer accepts.
            if new_password != self._password:
                self._password, self._fallback_password = new_password, self._password
            self._key_unresolved = True
            await tx.write(frame)
            await asyncio.sleep(CHANGE_KEY_SETTLE)
        outcome = await self._relink(RELINK_TIMEOUT)
        if outcome == "auth_failed":
            raise AuthFailed("the light accepted neither the new nor the previous password")
        if outcome == "timeout":
            raise ChangeUnconfirmed("no session could be opened to confirm the new password")
        if self._password != new_password:
            raise CommandFailed("the light did not accept the new password")

    async def _relink(self, timeout: float) -> str:
        """Drop the link and wait for the next session: ``ready``, ``auth_failed`` or ``timeout``."""
        before = self.session_count
        self._auth_failed = False
        self.request_link(timeout + 1.0)  # an on-demand link stays wanted for the whole wait
        self.request_drop("password change", intentional=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stopping:
                raise NotConnected("the link is stopping")
            if self.session_count > before and self._ready:
                return "ready"
            if self._auth_failed:
                return "auth_failed"
            await asyncio.sleep(0.05)
        return "timeout"

    # ------------------------------------------------------------------ waiting ---------

    async def _wait_ready(self, timeout: float, *, kick: bool = True) -> None:
        if self._ready:
            return
        if self._auth_failed:
            raise AuthFailed("the light refused the password")
        if self._stopping or self._latched:
            raise NotConnected("the link is stopping")
        self.request_link()
        if kick:
            self.kick()
        waiter: asyncio.Future[None] = self.hass.loop.create_future()
        self._ready_waiters.append(waiter)
        try:
            async with asyncio.timeout(timeout):
                await waiter
        except TimeoutError:
            raise NotConnected(f"the light did not connect within {timeout:g}s") from None
        finally:
            if waiter in self._ready_waiters:
                self._ready_waiters.remove(waiter)

    def _resolve_ready_waiters(self, error: Exception | None = None) -> None:
        for waiter in self._ready_waiters:
            if waiter.done():
                continue
            if error is None:
                waiter.set_result(None)
            else:
                waiter.set_exception(error)
        self._ready_waiters.clear()

    def _fail_waiters(self, error: Exception) -> None:
        """Fail everything waiting for a reply (the link that would answer is gone)."""
        for status_waiter in self._status_waiters:
            if not status_waiter.done():
                status_waiter.set_exception(error)
        self._status_waiters.clear()
        if self._key_waiter is not None and not self._key_waiter.done():
            self._key_waiter.set_exception(error)
        self._key_waiter = None
        for program_waiter in self._program_waiters.values():
            if not program_waiter.done():
                program_waiter.set_exception(error)
        self._program_waiters.clear()

    def _wanted(self) -> bool:
        if self._latched:
            return False
        if self._hold:
            return True
        now = time.monotonic()
        return now < self._want_until or now < self._linger_until

    def _touch(self) -> None:
        """Extend the idle timer of an on-demand link after an exchange."""
        if not self._hold:
            self._linger_until = max(self._linger_until, time.monotonic() + ON_DEMAND_LINGER)
            self._wake.set()

    # ------------------------------------------------------------------ supervisor ------

    async def _supervise(self) -> None:
        """Connect, hold, tear down, back off; never gives up while the entry is loaded."""
        try:
            while not self._stopping:
                if self._auth_failed or not self._wanted():
                    self._wake.clear()
                    if self._stopping or (not self._auth_failed and self._wanted()):
                        continue
                    await self._wake.wait()
                    continue
                reason = "failed"
                try:
                    reason = await self._cycle()
                except AuthFailed:
                    if not self._try_previous_key():
                        self._enter_auth_failed()
                    continue
                except asyncio.CancelledError:
                    raise
                except (LinkError, BleakError, TimeoutError, OSError, EOFError) as err:
                    self._record_failure(err)
                except Exception as err:  # noqa: BLE001 - the supervisor must survive anything
                    _LOGGER.warning("%s: unexpected link error", self.name, exc_info=True)
                    self._record_failure(err)
                if self._stopping:
                    break
                if reason == "failed":
                    await self._sleep(reconnect_delay(self.failures))
                elif reason == "dropped":
                    await self._sleep(DROP_RETRY_DELAY)
        finally:
            self._set_ready(False)

    async def _sleep(self, delay: float) -> None:
        """Back off for ``delay`` seconds.

        Ends early only when the supervisor is stopped, the light needs new credentials, an
        on-demand link is no longer wanted, or a retry was requested (a command's ``kick``, an
        advertisement) *while waiting here*. Any other wake-up is not a reason to reconnect.
        """
        end = time.monotonic() + delay
        self._kick_requested = False
        self._backing_off = True
        try:
            while not (self._stopping or self._auth_failed or self._kick_requested):
                if not self._hold and not self._wanted():
                    break
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), remaining)
        finally:
            self._backing_off = False
            self._kick_requested = False

    def _record_failure(self, err: BaseException) -> None:
        if self._stopping:
            return
        self.failures += 1
        self.last_error = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
        self._last_attempt_end = time.monotonic()
        log = _LOGGER.warning if self.failures % RECONNECT_WARN_EVERY == 0 else _LOGGER.debug
        log(
            "%s: connection attempt %s failed (%s); retrying in %.0fs",
            self.name,
            self.failures,
            self.last_error,
            reconnect_delay(self.failures, jitter=lambda _low, _high: 0.0),
        )
        self._emit(LinkEvent.FAILED)

    def _try_previous_key(self) -> bool:
        """The light refused a pending key: go back to the key that worked before the change."""
        if self._fallback_password is None:
            return False
        _LOGGER.info("%s: the light refused the new password; trying the previous one", self.name)
        self._password, self._fallback_password = self._fallback_password, None
        return True

    def _enter_auth_failed(self) -> None:
        self._auth_failed = True
        self._key_unresolved = False  # neither key works: nothing is pending any more
        self._fallback_password = None
        self.last_error = "the light refused the password"
        _LOGGER.warning("%s: the light refused the password; waiting for new credentials", self.name)
        self._resolve_ready_waiters(AuthFailed("the light refused the password"))
        self._emit(LinkEvent.AUTH_FAILED)

    async def _cycle(self) -> str:
        """One connect -> start-up -> hold -> teardown pass; returns why it ended."""
        device = self._current_device()
        if device is None:
            raise LinkError("the light is not visible to any Bluetooth adapter or proxy")
        self._last_attempt_start = time.monotonic()
        self.connect_attempts += 1
        client = await self._connect(device)
        reason = "failed"
        try:
            self._client = client
            self._disconnected.clear()
            self._drop_requested = None
            self._drop_intentional = False
            if not client.is_connected:
                raise BleakError("the link dropped right after connecting")
            self._resolve_characteristic(client)
            await self._init_session(client)
            self._mark_ready(client)
            reason = await self._hold_session()
        finally:
            was_ready = self._ready
            self._set_ready(False)
            self._fail_waiters(NotConnected("the link went away"))
            await self._teardown(client)
        if was_ready and reason in ("dropped", "requested"):
            if reason == "requested" and self._drop_intentional:
                self._emit(LinkEvent.RELINK)  # not a drop, but the entities have to follow the link down
                return "relink"
            self._drops.append(time.monotonic())
            self.last_drop = dt_util.utcnow()
            _LOGGER.info(
                "%s: link dropped (%s in the last hour); reconnecting",
                self.name,
                self.drops_in_window(),
            )
            self._emit(LinkEvent.DROPPED)
            return "dropped"
        if reason == "idle":
            self._emit(LinkEvent.IDLE)
        return reason

    async def _hold_session(self) -> str:
        """Hold the ready link until it drops, is asked to drop, idles out, or stops."""
        while True:
            self._wake.clear()
            if self._stopping:
                return "stopping"
            if self._drop_requested is not None:
                _LOGGER.debug("%s: dropping the link: %s", self.name, self._drop_requested)
                return "requested"
            if self._disconnected.is_set():
                return "dropped"
            timeout: float | None = None
            if not self._hold:
                remaining = max(self._linger_until, self._want_until) - time.monotonic()
                if remaining <= 0 and not self._busy:
                    return "idle"
                # A command owns (or waits for) the link: it is never released under it. The
                # command's end renews the deadline and wakes this loop.
                timeout = remaining if remaining > 0 else None
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout)

    # ------------------------------------------------------------------ connect ---------

    def _current_device(self) -> BLEDevice | None:
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        return device or self._last_device

    def _client_class_for_connect(self) -> type:
        # Resolved at connect time, not import time: Home Assistant's bluetooth integration
        # replaces ``BleakClientWithServiceCache`` with its own connection-tracking wrapper
        # during its setup, and only that wrapper has the backend-selection hooks.
        if self._client_class is None:
            self._client_class = make_affinity_client_class(
                bleak_retry_connector.BleakClientWithServiceCache,
                lambda: self._preferred_proxy() or None,
                on_choice=self._on_route_choice,
            )
        return self._client_class

    @callback
    def _on_route_choice(self, _scanner_name: str, preferred_used: bool) -> None:
        self.via_preferred_proxy = preferred_used

    async def _connect(self, device: BLEDevice) -> BleakClient:
        async with asyncio.timeout(CONNECT_DEADLINE):
            client = await establish_connection(
                self._client_class_for_connect(),
                device,
                self.name,
                disconnected_callback=self._on_disconnected,
                max_attempts=CONNECT_RETRIES,
                ble_device_callback=self._current_device_or_raise,
            )
        self._last_device = device
        if self._stopping or self._latched:
            await self._teardown(client)
            raise NotConnected("the link is stopping")
        return client

    def _current_device_or_raise(self) -> BLEDevice:
        device = self._current_device()
        if device is None:
            raise BleakError("the light is not visible to any Bluetooth adapter or proxy")
        return device

    @callback
    def _on_disconnected(self, client: BleakClient) -> None:
        if client is not self._client:
            return
        self._disconnected.set()
        self._wake.set()

    @callback
    def _on_advertisement(self, service_info: bluetooth.BluetoothServiceInfoBleak, _change: Any) -> None:
        self._last_device = service_info.device
        # Only a supervisor that is waiting in a backoff after failures can use it (see ``kick``).
        if self._ready or self._stopping or self._auth_failed or not self.failures or not self._backing_off:
            return
        if time.monotonic() - self._last_attempt_end >= ADVERT_KICK_MIN_GAP:
            self._kick_requested = True
            self._wake.set()

    def _resolve_characteristic(self, client: BleakClient) -> None:
        char = client.services.get_characteristic(protocol.CHAR_UUID)
        if char is None:
            raise BleakError(f"GATT characteristic {protocol.CHAR_UUID} not found: not a Pawfly light")
        self._char = char
        self._write_response = needs_write_response(char)

    async def _teardown(self, client: BleakClient) -> None:
        """Release the light: stop notifications, disconnect; bounded, never raises."""
        if self._client is client:
            self._client = None
        if self._notifying:
            self._notifying = False
            if client.is_connected:
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(STOP_NOTIFY_DEADLINE):
                        await client.stop_notify(self._char)
        with contextlib.suppress(Exception):
            async with asyncio.timeout(DISCONNECT_DEADLINE):
                await client.disconnect()

    # ------------------------------------------------------------------ session ---------

    async def _init_session(self, client: BleakClient) -> None:
        """subscribe -> verify key -> sync clock -> query status, all inside the GATT lock."""
        async with self._lock:
            await self._bounded(client.start_notify(self._char, self._on_notify), GATT_OP_DEADLINE, "start_notify")
            self._notifying = True

            try:
                key_frame = protocol.verify_key(self._password)
            except ValueError as err:
                raise AuthFailed(str(err)) from err
            key_waiter: asyncio.Future[protocol.KeyResult] = self.hass.loop.create_future()
            self._key_waiter = key_waiter
            await self._write_frame(client, key_frame)
            result: protocol.KeyResult | None = None
            try:
                async with asyncio.timeout(KEY_REPLY_TIMEOUT):
                    result = await key_waiter
            except TimeoutError:
                # The light answers the first key check of a new link; a missing answer is
                # not proof of a wrong key. The status query below is the real proof.
                _LOGGER.debug("%s: no reply to the key check; relying on the status query", self.name)
            finally:
                self._key_waiter = None
            if result is not None and not result.ok:
                raise AuthFailed("the light refused the password")

            await self._write_frame(client, protocol.time_sync(self._clock()))
            await self._query_status(client, STATUS_REPLY_TIMEOUT)

    def _mark_ready(self, client: BleakClient) -> None:
        self.session_count += 1
        self.failures = 0
        self._status_misses = 0
        self.last_error = None
        if self._key_unresolved:
            # The light accepted this key, so whatever was pending is settled: the new key became
            # the key, or the previous one stays it because the new one was refused.
            self._key_unresolved = False
            self._fallback_password = None
            if self._on_password is not None:
                try:
                    self._on_password(self._password)
                except Exception:  # noqa: BLE001 - a listener must never break the supervisor
                    _LOGGER.exception("%s: could not record the settled password", self.name)
        self.connected_since = dt_util.utcnow()
        scanner = getattr(client, "_connected_scanner", None)
        self.route_source = getattr(scanner, "source", None)
        self.route_adapter = getattr(scanner, "adapter", None)
        if not self._hold:
            self._linger_until = max(self._linger_until, time.monotonic() + ON_DEMAND_LINGER)
        _LOGGER.info(
            "%s: connected via %s",
            self.name,
            self.route_adapter or self.route_source or "a local adapter",
        )
        self._set_ready(True)
        if self._on_route is not None:
            self._on_route(self.route_source, self.route_adapter)

    def _set_ready(self, ready: bool) -> None:
        if self._ready == ready:
            return
        self._ready = ready
        if ready:
            self._resolve_ready_waiters()
            self._emit(LinkEvent.READY)
        else:
            self.connected_since = None

    def _emit(self, event: LinkEvent) -> None:
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - listeners must never break the supervisor
            _LOGGER.exception("%s: link event listener failed for %s", self.name, event)

    # ------------------------------------------------------------------ GATT ------------

    async def _bounded(self, awaitable: Any, timeout: float, what: str) -> Any:
        try:
            async with asyncio.timeout(timeout):
                return await awaitable
        except TimeoutError:
            self.last_error = f"{what} timed out after {timeout:g}s"
            self.request_drop(self.last_error)
            raise CommandFailed(self.last_error) from None

    async def _write_frame(self, client: BleakClient, frame: bytes) -> None:
        wait = self._last_write + FRAME_GAP - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        if client is not self._client or self._disconnected.is_set():
            raise NotConnected("the link dropped before the command could be written")
        try:
            await self._bounded(
                client.write_gatt_char(self._char, frame, response=self._write_response),
                GATT_OP_DEADLINE,
                "write",
            )
        except BleakError as err:
            self.last_error = f"write failed: {err}"
            self.request_drop(self.last_error)
            raise CommandFailed(self.last_error) from err
        self._last_write = time.monotonic()
        _LOGGER.debug("%s: wrote %s", self.name, "<key frame>" if _carries_key(frame) else frame.hex())

    async def _query_status(self, client: BleakClient, timeout: float) -> protocol.Status:
        waiter: asyncio.Future[protocol.Status] = self.hass.loop.create_future()
        self._status_waiters.append(waiter)
        try:
            await self._write_frame(client, protocol.query_status())
            async with asyncio.timeout(timeout):
                status = await waiter
        except TimeoutError:
            self._status_misses += 1
            self.last_error = f"no status reply within {timeout:g}s"
            if self._ready and self._status_misses >= STATUS_MISS_LIMIT:
                self.request_drop("status queries stopped being answered")
            raise CommandFailed(self.last_error) from None
        finally:
            if waiter in self._status_waiters:
                self._status_waiters.remove(waiter)
        self._status_misses = 0
        return status

    async def _read_program(self, client: BleakClient, program: int, timeout: float) -> list[protocol.Point]:
        waiter: asyncio.Future[list[protocol.Point]] = self.hass.loop.create_future()
        self._assembler = protocol.ProgramAssembler()
        self._program_waiters[program] = waiter
        try:
            await self._write_frame(client, protocol.query_program(program))
            async with asyncio.timeout(timeout):
                return await waiter
        except TimeoutError:
            raise CommandFailed(f"the light did not answer the query for program {program}") from None
        finally:
            if self._program_waiters.get(program) is waiter:
                del self._program_waiters[program]

    @callback
    def _on_notify(self, _char: Any, data: bytearray) -> None:
        """Parse one notification and route each message it carries."""
        try:
            raw = bytes(data)
            self.last_notification = raw.hex()
            self.last_notification_at = dt_util.utcnow()
            for message in decode(raw):
                self._dispatch(message)
        except Exception:  # noqa: BLE001 - a bad notification must never reach bleak's callback
            _LOGGER.exception("%s: could not handle notification %s", self.name, bytes(data).hex())

    def _dispatch(self, message: Any) -> None:
        if isinstance(message, protocol.Status):
            self.status = message
            for waiter in self._status_waiters:
                if not waiter.done():
                    waiter.set_result(message)
            self._on_status(message)
        elif isinstance(message, protocol.KeyResult):
            if self._key_waiter is not None and not self._key_waiter.done():
                self._key_waiter.set_result(message)
        elif isinstance(message, protocol.ProgramHeader | protocol.ProgramPoint):
            done = self._assembler.feed(message)
            if done is not None:
                program, points = done
                waiter = self._program_waiters.get(program)
                if waiter is not None and not waiter.done():
                    waiter.set_result(points)


async def async_check_password(hass: HomeAssistant, address: str, password: str, *, name: str = "") -> None:
    """Open a short-lived link and ask the light whether it accepts ``password``.

    Used by the config flow before an entry exists (and by reauth). Raises ``AuthFailed``
    when the light refuses the key and ``NotConnected`` when it cannot be reached or does
    not answer.
    """
    try:
        key_frame = protocol.verify_key(password)
    except ValueError as err:
        raise AuthFailed(str(err)) from err
    device = bluetooth.async_ble_device_from_address(hass, address.upper(), connectable=True)
    if device is None:
        raise NotConnected("the light is not visible to any Bluetooth adapter or proxy")

    loop = hass.loop
    result: asyncio.Future[protocol.KeyResult] = loop.create_future()

    @callback
    def _notified(_char: Any, data: bytearray) -> None:
        for message in decode(bytes(data)):
            if isinstance(message, protocol.KeyResult) and not result.done():
                result.set_result(message)

    client: BleakClient | None = None
    try:
        async with asyncio.timeout(CONNECT_DEADLINE):
            client = await establish_connection(
                bleak_retry_connector.BleakClientWithServiceCache,
                device,
                name or address,
                max_attempts=CONNECT_RETRIES,
                ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                    hass, address.upper(), connectable=True
                )
                or device,
            )
        char = client.services.get_characteristic(protocol.CHAR_UUID)
        if char is None:
            raise NotConnected("this is not a Pawfly light (characteristic ffe1 missing)")
        async with asyncio.timeout(GATT_OP_DEADLINE):
            await client.start_notify(char, _notified)
            await client.write_gatt_char(char, key_frame, response=needs_write_response(char))
        try:
            async with asyncio.timeout(KEY_REPLY_TIMEOUT + 1):
                reply = await result
        except TimeoutError:
            raise NotConnected("the light did not answer the password check") from None
    except (BleakError, TimeoutError, OSError, EOFError) as err:
        raise NotConnected(str(err) or type(err).__name__) from err
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(STOP_NOTIFY_DEADLINE):
                    await client.stop_notify(protocol.CHAR_UUID)
            with contextlib.suppress(Exception):
                async with asyncio.timeout(DISCONNECT_DEADLINE):
                    await client.disconnect()
    if not reply.ok:
        raise AuthFailed("the light refused the password")
