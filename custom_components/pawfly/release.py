"""Release the Bluetooth link cleanly, and resume it later.

Home Assistant does not unload config entries on shutdown, and ``bluetooth`` stops its stack on
``EVENT_HOMEASSISTANT_STOP``, so a link released only then can die without a completed
disconnect and leave the light believing it is still connected (it then stops advertising and
nobody can reach it). The clean path is to drop the link while Home Assistant and its Bluetooth
stack are both alive: ``__init__`` does that in one shutdown job per light (Home Assistant runs
those before it fires the stop event). ``<domain>.release_link`` stays for the same purpose on
demand (``script.safe_restart``), and the diagnostic ``release link`` button does it for one light
(for example to hand it to the vendor app).

Implemented by unloading the entry, because unload is the one proven teardown path (platforms
first, then the link supervisor, which disconnects with a hard deadline). What matters:

* the teardown is bounded (``RELEASE_DEADLINE`` per entry);
* the unload result is checked and a failure is reported honestly, never logged as success;
* the entry is set up again after ``resume_after`` seconds, but only if it still exists, is
  still enabled and nothing else set it up in the meantime; ``resume_after`` 0 keeps it released;
* the newest request decides: a release cancels the resume an earlier one scheduled (also when the
  light is already released), and setting the entry up by other means ends the pending resume too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from . import outage, shutdown
from .const import DOMAIN, RELEASE_DEADLINE

_LOGGER = logging.getLogger(__name__)

_RESUME = "resume"


def _resume_timers(hass: HomeAssistant) -> dict[str, CALLBACK_TYPE]:
    """The ``{entry_id: cancel}`` map of pending resumes."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    timers: dict[str, CALLBACK_TYPE] | None = domain_data.get(_RESUME)
    if timers is None:
        timers = domain_data[_RESUME] = {}
    return timers


@callback
def async_cancel_all_resumes(hass: HomeAssistant) -> None:
    """Forget every pending resume (Home Assistant is shutting down)."""
    timers = _resume_timers(hass)
    for cancel in timers.values():
        cancel()
    timers.clear()


@callback
def async_cancel_resume(hass: HomeAssistant, entry_id: str) -> bool:
    """Forget a pending resume (the entry was removed, set up by other means or released again).

    Returns whether one was pending.
    """
    cancel = _resume_timers(hass).pop(entry_id, None)
    if cancel is None:
        return False
    cancel()
    return True


@callback
def _schedule_resume(hass: HomeAssistant, entry_id: str, delay: int) -> None:
    timers = _resume_timers(hass)
    async_cancel_resume(hass, entry_id)

    async def _resume(_now: object) -> None:
        timers.pop(entry_id, None)
        if hass.is_stopping or shutdown.in_progress(hass):
            return
        entry = hass.config_entries.async_get_entry(entry_id)
        # Only the same entry, still enabled, still released: never resurrect a removed or
        # disabled entry and never fight a reload someone else already did.
        if entry is None or entry.disabled_by is not None or entry.state is not ConfigEntryState.NOT_LOADED:
            return
        _LOGGER.info("No restart followed release_link within %s s; setting %s up again", delay, entry.title)
        try:
            await hass.config_entries.async_setup(entry_id)
        except Exception:  # noqa: BLE001 - HA retries setup itself; never crash the timer
            _LOGGER.exception("Could not set %s up again after release_link", entry.title)

    timers[entry_id] = async_call_later(hass, delay, _resume)


async def async_release_entries(hass: HomeAssistant, entries: Iterable[ConfigEntry], resume_after: int) -> None:
    """Unload each loaded entry (dropping its link), resume them later, report failures.

    Raises ``HomeAssistantError`` naming every entry whose unload did not succeed; the ones
    that did unload are still scheduled to resume. An entry that is already released and waiting
    to resume gets the new schedule (``resume_after`` 0: it stays released).
    """
    released: list[ConfigEntry] = []
    failed: list[ConfigEntry] = []
    for entry in entries:
        # The newest request decides when, or whether, this light comes back.
        was_waiting = async_cancel_resume(hass, entry.entry_id)
        if entry.state is not ConfigEntryState.LOADED:
            if was_waiting:
                released.append(entry)  # still released: only its resume changes
            continue  # otherwise nothing is held: not loaded, or still retrying its setup
        try:
            async with asyncio.timeout(RELEASE_DEADLINE):
                unloaded = await hass.config_entries.async_unload(entry.entry_id)
        except Exception:  # noqa: BLE001 - timeout, OperationNotAllowed, anything: report it
            _LOGGER.exception("Releasing the link of %s failed", entry.title)
            unloaded = False
        if unloaded:
            _LOGGER.info("Released the Bluetooth link held for %s", entry.title)
            # Released on purpose: whatever outage was being counted is forgiven, otherwise the
            # time the light was deliberately handed over would count towards "unreachable".
            outage.async_forget(hass, entry)
            released.append(entry)
        else:
            _LOGGER.warning("Could not release the Bluetooth link held for %s", entry.title)
            failed.append(entry)

    if resume_after > 0:
        for entry in released:
            _schedule_resume(hass, entry.entry_id, resume_after)

    if failed:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="release_failed",
            translation_placeholders={"names": ", ".join(entry.title for entry in failed)},
        )
