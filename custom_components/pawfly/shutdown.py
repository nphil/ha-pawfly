"""The process-lifetime "Home Assistant is shutting down" latch.

Home Assistant runs its shutdown jobs (``hass.async_add_shutdown_job``) before it fires
``EVENT_HOMEASSISTANT_STOP``, and ``hass.state`` is still ``running`` while they run, so
``hass.is_stopping`` cannot be used to tell. Once the latch is set it is never cleared: nothing
in this integration connects, resumes, starts an outage clock or touches a repair again.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_KEY = "shutting_down"


def begin(hass: HomeAssistant) -> None:
    """Latch: Home Assistant is shutting down."""
    hass.data.setdefault(DOMAIN, {})[_KEY] = True


def in_progress(hass: HomeAssistant) -> bool:
    """Whether Home Assistant is shutting down (the latch is set)."""
    return bool(hass.data.get(DOMAIN, {}).get(_KEY))
