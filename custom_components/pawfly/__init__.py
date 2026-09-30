"""Pawfly Aquarium Light: local Bluetooth control of a Pawfly / PinYing PY4C WRGB light.

Component-level setup (the actions) happens once in ``async_setup``;
``async_setup_entry`` starts one persistent link per configured light and waits (bounded)
for its first session, so a wrong password surfaces as reauth and an unreachable light as
``ConfigEntryNotReady`` (Home Assistant retries; the outage clock and repair live outside the
entry, see ``outage.py``).
"""

from __future__ import annotations

import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import outage, services
from .const import DOMAIN
from .coordinator import PawflyConfigEntry, PawflyCoordinator
from .release import async_cancel_resume

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once (not per light)."""
    services.async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PawflyConfigEntry) -> bool:
    """Connect to the light, then set up its entities."""
    # However this setup came about (the resume timer itself, a reload, the user), a resume that an
    # earlier release_link left pending is over: it must not act on a later unload the user chose.
    async_cancel_resume(hass, entry.entry_id)
    coordinator = PawflyCoordinator(hass, entry)
    # Raises ConfigEntryAuthFailed / ConfigEntryNotReady, after stopping the link again.
    await coordinator.async_start()
    entry.runtime_data = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_shutdown()
        raise

    stop_fired = False

    async def _async_stop(_event: Event) -> None:
        """Home Assistant does not unload entries at shutdown: drop the link while Bluetooth lives."""
        nonlocal stop_fired
        stop_fired = True
        await coordinator.async_handle_stop()

    unsub_stop = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)

    @callback
    def _remove_stop_listener() -> None:
        # A one-time listener removes itself when it fires; removing it again is an error.
        if not stop_fired:
            unsub_stop()

    entry.async_on_unload(_remove_stop_listener)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PawflyConfigEntry) -> bool:
    """Unload the platforms, then release the light (bounded)."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: PawflyCoordinator = entry.runtime_data
        await coordinator.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: PawflyConfigEntry) -> None:
    """The entry is gone for good: forget its outage clock, repair and pending resume."""
    outage.async_forget(hass, entry)
    async_cancel_resume(hass, entry.entry_id)
