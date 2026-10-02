"""Pawfly Aquarium Light: local Bluetooth control of a Pawfly / PinYing PY4C WRGB light.

Component-level setup (the actions) happens once in ``async_setup``;
``async_setup_entry`` starts one persistent link per configured light and waits (bounded)
for its first session, so a wrong password surfaces as reauth and an unreachable light as
``ConfigEntryNotReady`` (Home Assistant retries; the outage clock and repair live outside the
entry, see ``outage.py``).

Home Assistant does not unload entries when it shuts down, so each light registers one shutdown
job that releases its link (see ``async_setup_entry``).
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic

from homeassistant.const import Platform
from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import outage, services, shutdown
from .const import DOMAIN
from .coordinator import PawflyConfigEntry, PawflyCoordinator
from .release import async_cancel_all_resumes, async_cancel_resume

_LOGGER = logging.getLogger(__name__)

#: Bound for releasing one light's link in Home Assistant's shutdown stage (all jobs share 20 s).
SHUTDOWN_RELEASE_TIMEOUT = 8.0

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@callback
def _quiet_for_shutdown(hass: HomeAssistant) -> None:
    """Latch the shutdown and stop what could act on it: outage clocks and pending resumes."""
    shutdown.begin(hass)
    outage.async_quiet(hass)
    async_cancel_all_resumes(hass)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once (not per light) and the shutdown latch."""
    services.async_setup_services(hass)

    async def _async_shutdown_latch() -> None:
        # Also covers a light that is released right now (no entry job to run): its resume must not fire.
        _quiet_for_shutdown(hass)

    hass.async_add_shutdown_job(HassJob(_async_shutdown_latch, "pawfly shutdown latch"))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PawflyConfigEntry) -> bool:
    """Connect to the light, then set up its entities."""
    if shutdown.in_progress(hass):
        raise ConfigEntryNotReady("Home Assistant is shutting down")
    # However this setup came about (the resume timer itself, a reload, the user), a resume that an
    # earlier release_link left pending is over: it must not act on a later unload the user chose.
    async_cancel_resume(hass, entry.entry_id)
    coordinator = PawflyCoordinator(hass, entry)

    async def _async_release_at_shutdown() -> None:
        """Home Assistant does not unload entries at shutdown: drop the link while Bluetooth lives.

        Runs in Home Assistant's shutdown stage, before ``bluetooth`` stops (on the stop event) and
        the proxies' connections close. Latches first, quiets the outage clocks and repairs, then
        releases the link only: the entry stays loaded and its entities keep their restored state.
        Bounded and never raises.
        """
        started = monotonic()
        try:
            _quiet_for_shutdown(hass)
            async with asyncio.timeout(SHUTDOWN_RELEASE_TIMEOUT):
                await coordinator.async_shutdown()
        except Exception as err:  # noqa: BLE001 - includes the timeout; a shutdown job must never raise
            _LOGGER.warning(
                "Releasing the BLE link to %s at shutdown failed after %.2f s: %s",
                entry.title,
                monotonic() - started,
                "timed out" if isinstance(err, TimeoutError) else repr(err),
            )
        else:
            _LOGGER.info("Released BLE link to %s at shutdown in %.2f s", entry.title, monotonic() - started)

    entry.async_on_unload(
        hass.async_add_shutdown_job(HassJob(_async_release_at_shutdown, f"pawfly release BLE link {entry.title}"))
    )
    # Raises ConfigEntryAuthFailed / ConfigEntryNotReady, after stopping the link again.
    await coordinator.async_start()
    entry.runtime_data = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_shutdown()
        raise
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
