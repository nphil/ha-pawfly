"""Diagnostics download of the Pawfly Aquarium Light.

The password (and a pending new one) are the only secrets in the entry and are redacted. The BLE
address and the proxy name stay: without them a link problem cannot be traced.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import outage
from .const import CONF_PENDING_PASSWORD
from .coordinator import PawflyConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_PENDING_PASSWORD, "new_password"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: PawflyConfigEntry) -> dict[str, Any]:
    """Return the entry, the link accounting and the last status, without the password."""
    coordinator = getattr(entry, "runtime_data", None)
    return {
        "entry": {
            "title": entry.title,
            "state": entry.state.value,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "outage_seconds": outage.outage_seconds(hass, str(entry.data[CONF_ADDRESS])),
        "coordinator": None if coordinator is None else coordinator.diagnostics(),
    }
