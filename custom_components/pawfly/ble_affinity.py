"""Preferred-proxy affinity for Home Assistant Bluetooth connections.

Vendored verbatim into each nateshome BLE integration (ac_infinity, bedjet,
fluvalble, ecoflow_iot). Change it here first
(`/data/home/tmp/ble_affinity.py` is the source of truth) and copy to every
integration in the same pass.

Why this exists
---------------
`HaBleakClientWrapper.connect()` (habluetooth `wrappers.py`) ignores the
`BLEDevice` an integration hands it and re-picks the connection path on
every connect: sorted by advertisement RSSI, then
`BaseHaScanner._score_connection_paths` (penalties for connections in
progress, prior failures and a last free slot). An integration therefore
has NO supported way to say "connect through the proxy in this room";
habluetooth #602 asks for one and is still open.

The distance the RSSI sort ignores is what matters here. Every ghost link
seen on this network (2026-09-09 .. 2026-09-17) formed on a marginal link
to a *distant* proxy: the disconnect handshake failed to complete over the
weak path and left the peripheral holding a connection the proxy had
forgotten. A device that is always carried by the proxy sitting next to it
does not get into that state.

How
---
`make_affinity_client_class(base, ...)` returns a subclass of whatever
client class `bleak_retry_connector.establish_connection` would otherwise
use and overrides ONE method, `_async_get_best_available_backend_and_device`.
When the preferred scanner currently advertises the address, can accept a
connection, and has not failed this address `max_failures` times in a row,
it is chosen; in every other case the default selection runs unchanged.
All of the wrapper's bookkeeping (`_add_connecting`, `_track`, slot
release, abort-on-unregister) still runs because `connect()` is untouched.

Fallback is therefore bounded by habluetooth's own failure counter, which
resets on the next successful connect through that scanner:
`establish_connection` retries, the first `max_failures` attempts go to the
preferred proxy, then the default path takes over for the rest.

The preferred scanner is named by its ESPHome node name (`scanner.adapter`,
what `bleak_esphome` registers from `device_info.name`), the same identity
the integrations already store in `last_holding_proxy`. Source MAC and the
full `scanner.name` are accepted too.

Private-API note: the overridden method and `_async_get_backend_for_ble_device`
are habluetooth internals (present in 6.26.x). `affinity_supported()` checks
for them; when absent the factory returns `base` unchanged and logs once, so
an upgrade degrades to default routing rather than breaking connections.

Leaving a path out for one attempt
----------------------------------
A proxy whose connect or subscribe just hung must not be picked again by the very next retry, and
habluetooth cannot say so (a connect that succeeded before the hang resets that proxy's failure score).
``excluded_getter`` names such a scanner; its paths are filtered out of the list the selector sees, so no
connection slot is ever reserved on it, provided another connectable path exists.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from bleak.exc import BleakError

_LOGGER = logging.getLogger(__name__)

# Preferred scanner gives up after this many consecutive failures for one
# address (habluetooth resets the counter on success). Three is the point at
# which the default scorer would itself have ranked the scanner below a
# healthy alternative.
DEFAULT_MAX_FAILURES = 3

_SELECT = "_async_get_best_available_backend_and_device"
_BACKEND_FOR = "_async_get_backend_for_ble_device"
# HaBleakClientWrapper skips BleakClient.__init__ and keeps the address in a
# name-mangled attribute; there is no public accessor before connect().
_WRAPPER_ADDRESS = "_HaBleakClientWrapper__address"
_warned_unsupported = False


def scanner_matches(scanner: Any, preferred: str) -> bool:
    """Return True if ``scanner`` is the one the operator named."""
    if not preferred:
        return False
    wanted = preferred.strip().lower()
    for attr in ("adapter", "source", "name"):
        value = getattr(scanner, attr, None)
        if isinstance(value, str) and value.strip().lower() == wanted:
            return True
    return False


def affinity_supported(base: type) -> bool:
    """Return True if ``base`` exposes the habluetooth hooks this relies on."""
    return callable(getattr(base, _SELECT, None)) and callable(
        getattr(base, _BACKEND_FOR, None)
    )


def _client_address(client: Any) -> str:
    """Return the target address of a not-yet-connected wrapper client."""
    address = getattr(client, _WRAPPER_ADDRESS, None)
    if isinstance(address, str):
        return address
    return str(getattr(client, "address", ""))


class _FilteredManager:
    """The Bluetooth manager as seen by one selection, with some connection paths left out.

    habluetooth's selector asks the manager for the paths to an address and reserves a connection slot
    only for the one it returns, so filtering the list here keeps an excluded scanner out of the choice
    BEFORE any slot is reserved (nothing to release afterwards). Every other manager call is delegated.
    """

    __slots__ = ("_address", "_devices", "_manager")

    def __init__(self, manager: Any, address: str, devices: list[Any]) -> None:
        self._manager = manager
        self._address = address.upper()
        self._devices = devices

    def async_scanner_devices_by_address(self, address: str, connectable: bool) -> Any:
        if address.upper() == self._address:
            return self._devices
        return self._manager.async_scanner_devices_by_address(address, connectable)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)


def make_affinity_client_class(
    base: type,
    preferred_getter: Callable[[], str | None],
    *,
    max_failures: int = DEFAULT_MAX_FAILURES,
    on_choice: Callable[[str, bool], None] | None = None,
    excluded_getter: Callable[[], str | None] | None = None,
) -> type:
    """Return ``base`` specialised to prefer one scanner and to leave one out for an attempt.

    ``preferred_getter`` is called at each connect so an options change
    takes effect on the next reconnect without rebuilding the client.
    ``on_choice(scanner_name, preferred_used)`` is invoked after every
    selection so the caller can surface which path was taken.
    ``excluded_getter`` names a scanner (node name, source or scanner name)
    that must not be chosen for this attempt, for example the proxy whose
    connect or subscribe just hung. It is honoured only while another
    connectable path to the device exists and can take the connection:
    otherwise the unfiltered selection runs, so an exclusion can never turn
    a reachable device into an unreachable one.
    """
    global _warned_unsupported
    if not affinity_supported(base):
        if not _warned_unsupported:
            _LOGGER.warning(
                "Bluetooth client %s has no backend-selection hook; "
                "preferred-proxy affinity is disabled and habluetooth's "
                "default routing applies",
                getattr(base, "__name__", base),
            )
            _warned_unsupported = True
        return base

    default_select = getattr(base, _SELECT)

    def _choose(self: Any, manager: Any) -> Any:
        preferred = preferred_getter()
        if not preferred:
            backend = default_select(self, manager)
            if on_choice is not None:
                on_choice(getattr(backend.scanner, "name", "?"), False)
            return backend

        address = _client_address(self)
        for scanner_device in manager.async_scanner_devices_by_address(address, True):
            scanner = scanner_device.scanner
            if not scanner_matches(scanner, preferred):
                continue
            connector = getattr(scanner, "connector", None)
            if connector is None or not connector.can_connect():
                _LOGGER.debug(
                    "%s: preferred proxy %s has no free connection slot; "
                    "falling back to default routing",
                    address,
                    scanner.name,
                )
                break
            failures = scanner.connection_failures(address)
            if failures >= max_failures:
                _LOGGER.info(
                    "%s: preferred proxy %s failed %d times in a row; "
                    "falling back to default routing until it succeeds again",
                    address,
                    scanner.name,
                    failures,
                )
                break
            backend = getattr(self, _BACKEND_FOR)(
                manager, scanner, scanner_device.ble_device
            )
            if backend is None:
                break
            _LOGGER.info(
                "%s: connecting via preferred proxy %s (RSSI %s)",
                address,
                scanner.name,
                scanner_device.advertisement.rssi,
            )
            if on_choice is not None:
                on_choice(scanner.name, True)
            return backend
        else:
            _LOGGER.debug(
                "%s: preferred proxy %s does not currently see this device; "
                "falling back to default routing",
                address,
                preferred,
            )

        backend = default_select(self, manager)
        if on_choice is not None:
            on_choice(getattr(backend.scanner, "name", "?"), False)
        return backend

    def _select(self: Any, manager: Any) -> Any:
        excluded = excluded_getter() if excluded_getter is not None else None
        if excluded:
            address = _client_address(self)
            devices = list(manager.async_scanner_devices_by_address(address, True))
            others = [
                device for device in devices if not scanner_matches(device.scanner, excluded)
            ]
            if others and len(others) < len(devices):
                try:
                    backend = _choose(self, _FilteredManager(manager, address, others))
                except BleakError:
                    # The other paths cannot take the connection right now (no free slot): a failed
                    # selection reserved nothing, so fall through to the unfiltered one.
                    _LOGGER.debug(
                        "%s: no other path than %s can connect; not excluding it",
                        address,
                        excluded,
                    )
                else:
                    _LOGGER.info(
                        "%s: leaving out %s for this attempt, connecting via %s",
                        address,
                        excluded,
                        getattr(backend.scanner, "name", "?"),
                    )
                    return backend
        return _choose(self, manager)

    return type(
        f"Affinity{base.__name__}",
        (base,),
        {_SELECT: _select, "__module__": __name__},
    )
