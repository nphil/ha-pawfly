"""Process-scoped outage clock and the "light unreachable" repair.

How long a light has been unreachable is kept per BLE address in ``hass.data[DOMAIN]``,
never on the coordinator (or the config entry runtime): a coordinator is rebuilt on every
reload, so a clock owned by it would restart from zero exactly when an outage is long (the
failure the house BLE skill describes). The clock is armed when the link is lost or setup
finished without a session (the light keeps connecting in the background) and is cleared only
by a genuine recovery (a session was established: key accepted, status read) or by removing
the entry. Advertisements alone never clear it.

The repair is reconciled against the actual issue registry and the live state of the entry
on every step, so it converges after a reload instead of relying on remembered state. A loaded
entry whose link is not up, and one waiting in ``SETUP_RETRY``, count as unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later

from . import shutdown
from .const import CONF_LAST_HOLDING_PROXY, DOMAIN

_LOGGER = logging.getLogger(__name__)

#: How long the link has to stay down before the repairs panel is involved. Shorter outages
#: (a proxy reboot, the vendor app holding the light for a while) heal by themselves.
UNREACHABLE_AFTER = 15 * 60.0

ISSUE_TRANSLATION_KEY = "unreachable"
_OUTAGES = "outages"
#: Entry states in which a down link is an outage: running, being set up, waiting to retry.
_OUTAGE_STATES = frozenset(
    {ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS, ConfigEntryState.SETUP_RETRY}
)


@dataclass
class _Outage:
    """One light's running outage."""

    entry_id: str
    since: float
    unsub: CALLBACK_TYPE | None = None


def issue_id_for(address: str) -> str:
    """Repairs issue id of one light."""
    return f"{address.replace(':', '').lower()}_unreachable"


def _outages(hass: HomeAssistant) -> dict[str, _Outage]:
    """The process-scoped ``{ADDRESS: outage}`` map."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    outages: dict[str, _Outage] | None = domain_data.get(_OUTAGES)
    if outages is None:
        outages = domain_data[_OUTAGES] = {}
    return outages


@callback
def async_quiet(hass: HomeAssistant) -> None:
    """Home Assistant is shutting down: stop every outage timer.

    The links dropped on purpose are then not mistaken for an outage and no repair is created;
    the issue registry is left exactly as it is.
    """
    for outage in _outages(hass).values():
        _cancel_timer(outage)


def _cancel_timer(outage: _Outage) -> None:
    unsub, outage.unsub = outage.unsub, None
    if unsub is not None:
        unsub()


def outage_seconds(hass: HomeAssistant, address: str) -> float | None:
    """Seconds the light has been unreachable, or ``None`` when no outage is recorded."""
    outage = _outages(hass).get(address.upper())
    return None if outage is None else monotonic() - outage.since


@callback
def async_link_lost(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The link is down or could not be established: start the clock if it is not running.

    Idempotent. The first drop of an outage is recorded once and never restamped, so a
    reload in the middle of an outage keeps counting from the original drop.
    """
    if shutdown.in_progress(hass):
        return
    if _auth_rejected(entry):
        return
    address = str(entry.data[CONF_ADDRESS]).upper()
    outages = _outages(hass)
    outage = outages.get(address)
    if outage is None:
        outage = outages[address] = _Outage(entry_id=entry.entry_id, since=monotonic())
    else:
        outage.entry_id = entry.entry_id
    _reconcile(hass, address)


@callback
def async_link_recovered(hass: HomeAssistant, address: str) -> None:
    """A session was established: the outage is over (clock, timer and repair)."""
    if shutdown.in_progress(hass):
        return
    _clear(hass, address.upper())


@callback
def async_auth_rejected(hass: HomeAssistant, address: str) -> None:
    """The light refused the password: it is reachable, so whatever outage was counted is over.

    Clears the clock, its timer and the repair; the reauth flow is what asks the user to act. No outage
    is started again while the link stays rejected.
    """
    if shutdown.in_progress(hass):
        return
    _clear(hass, address.upper())


def _auth_rejected(entry: ConfigEntry) -> bool:
    """Whether the entry's link currently has its password refused (live state)."""
    coordinator: Any = getattr(entry, "runtime_data", None)
    return bool(getattr(getattr(coordinator, "link", None), "auth_failed", False))


@callback
def async_forget(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The entry was removed: forget its outage and delete its repair."""
    _clear(hass, str(entry.data.get(CONF_ADDRESS, "")).upper())


def _clear(hass: HomeAssistant, address: str) -> None:
    outage = _outages(hass).pop(address, None)
    if outage is not None:
        _cancel_timer(outage)
    if address:
        ir.async_delete_issue(hass, DOMAIN, issue_id_for(address))


def _link_healthy(entry: ConfigEntry) -> bool:
    """Whether the entry currently has a working link (live state, not a cached hint)."""
    coordinator: Any = getattr(entry, "runtime_data", None)
    return entry.state is ConfigEntryState.LOADED and bool(getattr(coordinator, "link_healthy", False))


@callback
def _reconcile(hass: HomeAssistant, address: str) -> None:
    """Converge clock, timer and repair on the light's live state."""
    if shutdown.in_progress(hass):
        return
    outage = _outages(hass).get(address)
    if outage is None:
        return
    entry = hass.config_entries.async_get_entry(outage.entry_id)
    if entry is None or entry.disabled_by is not None or _link_healthy(entry) or _auth_rejected(entry):
        _clear(hass, address)
        return
    if entry.state not in _OUTAGE_STATES:
        # Unloaded (release_link, a reload in flight, a manual unload) or failed for a reason
        # with its own repair (bad password -> reauth): the link is not expected to be up, so
        # this is not an outage to report. The clock is kept: the next failed attempt of a
        # retried setup re-arms the deadline for whatever is left of the window.
        _cancel_timer(outage)
        ir.async_delete_issue(hass, DOMAIN, issue_id_for(address))
        return

    remaining = UNREACHABLE_AFTER - (monotonic() - outage.since)
    if remaining > 0:
        # A dead link produces no further events, so the deadline has to come from a timer.
        if outage.unsub is None:

            @callback
            def _deadline(_now: datetime) -> None:
                _on_deadline(hass, address)

            outage.unsub = async_call_later(hass, remaining, _deadline)
        return

    _cancel_timer(outage)
    _set_issue(hass, entry, address)


@callback
def _on_deadline(hass: HomeAssistant, address: str) -> None:
    outage = _outages(hass).get(address)
    if outage is not None:
        outage.unsub = None
    _reconcile(hass, address)


def _set_issue(hass: HomeAssistant, entry: ConfigEntry, address: str) -> None:
    """Create the repair unless the registry already holds the same rendering of it."""
    placeholders = {
        "name": entry.title,
        "minutes": str(int(UNREACHABLE_AFTER // 60)),
        "proxy": str(entry.data.get(CONF_LAST_HOLDING_PROXY) or "unknown"),
    }
    issue_id = issue_id_for(address)
    existing = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    if (
        existing is not None
        and existing.severity == ir.IssueSeverity.WARNING
        and existing.translation_key == ISSUE_TRANSLATION_KEY
        and existing.translation_placeholders == placeholders
    ):
        return
    _LOGGER.warning("%s has been unreachable for %s minutes", entry.title, placeholders["minutes"])
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_TRANSLATION_KEY,
        translation_placeholders=placeholders,
    )
