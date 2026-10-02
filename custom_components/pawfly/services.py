"""Actions of the Pawfly integration (registered once, from ``async_setup``).

Every action targets one light through ``device_id`` (the Home Assistant device id), except
``release_link`` where it is optional (all lights when omitted, like the other BLE
integrations' ``release_link``).

Malformed input is rejected by the schema (``vol.Invalid``, websocket error ``invalid_format``);
well-formed input that cannot be acted on raises ``ServiceValidationError`` (websocket error
``service_validation_error``); link and device failures raise ``HomeAssistantError``
(``home_assistant_error``). Reading a built-in program (0-2) needs no Bluetooth: those curves
live in the app, not in the light.
"""

from __future__ import annotations

import math
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from . import protocol
from .const import (
    ATTR_DEVICE_ID,
    ATTR_END,
    ATTR_NAME,
    ATTR_NEW_PASSWORD,
    ATTR_POINTS,
    ATTR_PROGRAM,
    ATTR_RESUME_AFTER,
    DEFAULT_RESUME_AFTER,
    DOMAIN,
    MAX_RESUME_AFTER,
    PASSWORD_LENGTH,
    SERVICE_CHANGE_PASSWORD,
    SERVICE_GET_PROGRAM,
    SERVICE_PREVIEW,
    SERVICE_RELEASE_LINK,
    SERVICE_RENAME,
    SERVICE_SET_CHANNEL,
    SERVICE_SET_PROGRAM,
    SERVICE_SYNC_TIME,
)
from .coordinator import PawflyCoordinator, ProgramReading
from .model import CHANNELS, PROGRAM_SLUGS, is_diy, parse_program, point_to_dict
from .release import async_release_entries

_TIME_RE = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


def _percent(value: Any) -> int:
    """A whole-number percentage 0-100 (ints, or floats that are whole numbers)."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise vol.Invalid("must be a whole number from 0 to 100")
    try:
        number = float(value)
    except ValueError as err:
        raise vol.Invalid("must be a whole number from 0 to 100") from err
    # inf/nan/1e999 must be rejected here: int(inf) would raise OverflowError, not a validation error.
    if not math.isfinite(number) or number != int(number) or not 0 <= number <= 100:
        raise vol.Invalid("must be a whole number from 0 to 100")
    return int(number)


def _program(value: Any) -> int:
    """A program index 0-5 or one of the program names (``diy_1`` ...)."""
    try:
        return parse_program(value)
    except ValueError as err:
        options = ", ".join(PROGRAM_SLUGS)
        raise vol.Invalid(f"{err}; use 0-5 or one of: {options}") from err


_POINT = vol.Schema(
    {
        vol.Required("time"): vol.All(str, vol.Match(_TIME_RE, msg="use HH:MM in 24-hour time")),
        **{vol.Optional(channel, default=0): _percent for channel in CHANNELS},
    }
)


def _points(value: Any) -> list[dict[str, Any]]:
    """Validate a program's points: at most the app's maximum, each time used once."""
    if not isinstance(value, list):
        raise vol.Invalid("points must be a list")
    if len(value) > protocol.MAX_PROGRAM_POINTS:
        raise vol.Invalid(f"a program holds at most {protocol.MAX_PROGRAM_POINTS} points, got {len(value)}")
    points = [_POINT(point) for point in value]
    times = [point["time"] for point in points]
    for time in times:
        if times.count(time) > 1:
            raise vol.Invalid(f"two points at {time}: every time can be used once")
    return points


def _some_channel(value: dict[str, Any]) -> dict[str, Any]:
    """At least one channel must be given (only those are written)."""
    if not any(channel in value for channel in CHANNELS):
        raise vol.Invalid("give at least one of red, green, blue, white")
    return value


def _preview(value: dict[str, Any]) -> dict[str, Any]:
    """Either ``end: true`` or at least one channel (missing channels are 0), never both."""
    has_channels = any(channel in value for channel in CHANNELS)
    if value[ATTR_END]:
        if has_channels:
            raise vol.Invalid("give either end: true or colour values, not both")
        return value
    if not has_channels:
        raise vol.Invalid("give colour values (red, green, blue, white) or end: true")
    return {**value, **{channel: value.get(channel, 0) for channel in CHANNELS}}


_DEVICE = {vol.Required(ATTR_DEVICE_ID): cv.string}

GET_PROGRAM_SCHEMA = vol.Schema({**_DEVICE, vol.Required(ATTR_PROGRAM): _program})
SET_PROGRAM_SCHEMA = vol.Schema({**_DEVICE, vol.Required(ATTR_PROGRAM): _program, vol.Required(ATTR_POINTS): _points})
SET_CHANNEL_SCHEMA = vol.All(
    vol.Schema({**_DEVICE, **{vol.Optional(channel): _percent for channel in CHANNELS}}),
    _some_channel,
)
PREVIEW_SCHEMA = vol.All(
    vol.Schema(
        {
            **_DEVICE,
            **{vol.Optional(channel): _percent for channel in CHANNELS},
            vol.Optional(ATTR_END, default=False): cv.boolean,
        }
    ),
    _preview,
)
SYNC_TIME_SCHEMA = vol.Schema(_DEVICE)
RENAME_SCHEMA = vol.Schema(
    {**_DEVICE, vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1, max=protocol.NAME_MAX_LENGTH))}
)
CHANGE_PASSWORD_SCHEMA = vol.Schema(
    {
        **_DEVICE,
        vol.Required(ATTR_NEW_PASSWORD): vol.All(
            cv.string, vol.Match(rf"^[0-9]{{{PASSWORD_LENGTH}}}$", msg=f"the password is exactly {PASSWORD_LENGTH} digits")
        ),
    }
)
RELEASE_LINK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_RESUME_AFTER, default=DEFAULT_RESUME_AFTER): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_RESUME_AFTER)
        ),
    }
)


def _entry_for_device(hass: HomeAssistant, device_id: str) -> ConfigEntry:
    """The Pawfly config entry behind a device id (loaded or not)."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.domain == DOMAIN:
            return entry
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="not_a_pawfly_device",
        translation_placeholders={"device_id": device_id},
    )


def _coordinator_for_device(hass: HomeAssistant, device_id: str) -> PawflyCoordinator:
    entry = _entry_for_device(hass, device_id)
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"name": entry.title},
        )
    return entry.runtime_data


def _program_response(reading: ProgramReading, **extra: Any) -> dict[str, Any]:
    program = reading.program
    return {
        "program": program,
        "slug": PROGRAM_SLUGS[program],
        "name": protocol.PROGRAM_NAMES[program],
        "source": reading.source,
        "editable": is_diy(program),
        **extra,
        "points": [point_to_dict(point) for point in reading.points],
    }


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""

    async def get_program(call: ServiceCall) -> dict[str, Any]:
        program: int = call.data[ATTR_PROGRAM]
        if not is_diy(program):
            # Served from the app's own curves; the light would not answer, and no link is needed.
            _entry_for_device(hass, call.data[ATTR_DEVICE_ID])
            reading = ProgramReading(program, "preset", tuple(protocol.PRESETS[program]))
        else:
            reading = await _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID]).async_get_program(program)
        return _program_response(reading)

    async def set_program(call: ServiceCall) -> dict[str, Any]:
        program: int = call.data[ATTR_PROGRAM]
        if not is_diy(program):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="program_read_only",
                translation_placeholders={"program": protocol.PROGRAM_NAMES[program]},
            )
        coordinator = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        points = [
            protocol.Point(
                hour=int(point["time"][:2]),
                minute=int(point["time"][3:]),
                red=point["red"],
                green=point["green"],
                blue=point["blue"],
                white=point["white"],
            )
            for point in call.data[ATTR_POINTS]
        ]
        reading = await coordinator.async_write_program(program, points)
        return _program_response(reading, count=len(reading.points), verified=True)

    async def set_channel(call: ServiceCall) -> None:
        coordinator = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_set_channels({channel: call.data[channel] for channel in CHANNELS if channel in call.data})

    async def preview(call: ServiceCall) -> None:
        coordinator = _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID])
        if call.data[ATTR_END]:
            await coordinator.async_preview(None)
        else:
            await coordinator.async_preview(tuple(call.data[channel] for channel in CHANNELS))  # type: ignore[arg-type]

    async def sync_time(call: ServiceCall) -> None:
        await _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID]).async_sync_time()

    async def rename(call: ServiceCall) -> None:
        await _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID]).async_rename(call.data[ATTR_NAME])

    async def change_password(call: ServiceCall) -> None:
        await _coordinator_for_device(hass, call.data[ATTR_DEVICE_ID]).async_change_password(
            call.data[ATTR_NEW_PASSWORD]
        )

    async def release_link(call: ServiceCall) -> None:
        if device_id := call.data.get(ATTR_DEVICE_ID):
            entries = [_entry_for_device(hass, device_id)]
        else:
            entries = hass.config_entries.async_entries(DOMAIN)
        await async_release_entries(hass, entries, call.data[ATTR_RESUME_AFTER])

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PROGRAM,
        get_program,
        schema=GET_PROGRAM_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_PROGRAM,
        set_program,
        schema=SET_PROGRAM_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_SET_CHANNEL, set_channel, schema=SET_CHANNEL_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_PREVIEW, preview, schema=PREVIEW_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SYNC_TIME, sync_time, schema=SYNC_TIME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RENAME, rename, schema=RENAME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHANGE_PASSWORD, change_password, schema=CHANGE_PASSWORD_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RELEASE_LINK, release_link, schema=RELEASE_LINK_SCHEMA)
