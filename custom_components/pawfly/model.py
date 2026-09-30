"""Pure mappings between the light's wire values and Home Assistant's scales.

No Home Assistant imports: everything here is plain arithmetic/lookup so the entity
platforms, the services and the tests share one definition.
"""

from __future__ import annotations

from typing import Any

from . import protocol
from .const import EFFECT_DEMO

#: ``select.program`` option that means "no stored program or scene runs".
OPTION_MANUAL = "manual"
#: Slugs of the six stored programs, in program-index order (0-2 built-in, 3-5 DIY 1-3).
PROGRAM_SLUGS: tuple[str, ...] = tuple(name.lower().replace(" ", "_") for name in protocol.PROGRAM_NAMES)
PROGRAM_OPTIONS: tuple[str, ...] = (OPTION_MANUAL, *PROGRAM_SLUGS)

MODE_MANUAL = "manual"
MODE_SCENE = "scene"
MODE_PROGRAM = "program"
MODE_KEYS: dict[protocol.Mode, str] = {
    protocol.Mode.MANUAL: MODE_MANUAL,
    protocol.Mode.SCENARIO: MODE_SCENE,
    protocol.Mode.PROGRAM: MODE_PROGRAM,
}
MODE_OPTIONS: tuple[str, ...] = (MODE_MANUAL, MODE_SCENE, MODE_PROGRAM)

#: Effects offered by the light entity: the eight scenarios (device order), Demo last.
EFFECT_LIST: list[str] = [*protocol.SCENARIO_NAMES, EFFECT_DEMO]

RGBW = tuple[int, int, int, int]

#: Channel names in wire order: the channel byte of opcode 0x04 is the index (0=R 1=G 2=B 3=W).
CHANNELS: tuple[str, ...] = protocol.CHANNEL_NAMES


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def brightness_to_ha(percent: int) -> int:
    """Device master brightness (1-100) -> HA brightness (1-255)."""
    return _clamp(round(percent * 255 / 100), 1, 255)


def brightness_to_device(value: int) -> int:
    """HA brightness (1-255) -> device master brightness (1-100)."""
    return _clamp(round(value * 100 / 255), protocol.BRIGHTNESS_MIN, protocol.BRIGHTNESS_MAX)


def channel_to_ha(percent: int) -> int:
    """Device channel level (0-100 %) -> HA colour component (0-255); round-trips exactly."""
    return _clamp(round(percent * 255 / 100), 0, 255)


def channel_to_device(value: int) -> int:
    """HA colour component (0-255) -> device channel level (0-100 %)."""
    return _clamp(round(value * 100 / 255), 0, protocol.COLOR_MAX)


def display_rgbw(status: protocol.Status) -> RGBW:
    """The colour the light shows, in percent.

    The status frame carries the last *manual* colour register only: it does not change
    while a scenario or program runs (verified live). In a scenario the app displays its own
    mirror colours (``protocol.SCENARIO_COLORS``), so this does too; in manual and program
    mode it is the register itself.
    """
    if status.mode is protocol.Mode.SCENARIO and status.scenario is not None:
        if 0 <= status.scenario < len(protocol.SCENARIO_COLORS):
            return protocol.SCENARIO_COLORS[status.scenario]
    return (status.red, status.green, status.blue, status.white)


def mode_key(status: protocol.Status) -> str:
    """``manual`` / ``scene`` / ``program``."""
    return MODE_KEYS[status.mode]


def program_option(status: protocol.Status) -> str | None:
    """Current ``select.program`` option; ``None`` while a scene runs (no option fits)."""
    if status.mode is protocol.Mode.MANUAL:
        return OPTION_MANUAL
    if status.mode is protocol.Mode.PROGRAM and status.program is not None:
        if 0 <= status.program < len(PROGRAM_SLUGS):
            return PROGRAM_SLUGS[status.program]
    return None


def scenario_name(index: int | None) -> str | None:
    """Scenario index -> name (``None`` when unknown)."""
    if index is not None and 0 <= index < len(protocol.SCENARIO_NAMES):
        return protocol.SCENARIO_NAMES[index]
    return None


def program_name(index: int | None) -> str | None:
    """Program index -> vendor name (``None`` when unknown)."""
    if index is not None and 0 <= index < len(protocol.PROGRAM_NAMES):
        return protocol.PROGRAM_NAMES[index]
    return None


def parse_program(value: Any) -> int:
    """A program given as an index (0-5, int or numeric string) or a slug -> program index.

    Raises ``ValueError`` for anything else.
    """
    if isinstance(value, bool):
        raise ValueError("program must be an index 0-5 or a program name")
    if isinstance(value, int):
        index = value
    elif isinstance(value, str):
        text = value.strip().lower()
        if text in PROGRAM_SLUGS:
            return PROGRAM_SLUGS.index(text)
        try:
            index = int(text)
        except ValueError as err:
            raise ValueError(f"unknown program {value!r}") from err
    else:
        raise ValueError("program must be an index 0-5 or a program name")
    if not 0 <= index <= protocol.LAST_PROGRAM:
        raise ValueError(f"program index must be 0-{protocol.LAST_PROGRAM}, got {index}")
    return index


def is_pawfly_name(local_name: str | None) -> bool:
    """True for an advertised name the vendor app recognises (``PY4C...`` / ``PYLamp-4C...``).

    The advertisement carries the local name only (flags + name, verified live), no service UUID.
    """
    return local_name is not None and local_name.startswith(protocol.ADVERTISED_PREFIXES)


def display_name(local_name: str | None) -> str:
    """The name the vendor app shows for an advertised name (``""`` when there is none)."""
    return protocol.display_name(local_name) if local_name else ""


def is_diy(program: int) -> bool:
    """True for the three programs the user can rewrite (DIY 1-3)."""
    return protocol.FIRST_DIY_PROGRAM <= program <= protocol.LAST_PROGRAM


def format_time(point: protocol.Point) -> str:
    """``HH:MM`` of a program point."""
    return f"{point.hour:02d}:{point.minute:02d}"


def point_to_dict(point: protocol.Point) -> dict[str, Any]:
    """A program point in the service/response shape (percent channels)."""
    return {
        "time": format_time(point),
        "red": point.red,
        "green": point.green,
        "blue": point.blue,
        "white": point.white,
    }


def point_from_dict(data: dict[str, Any]) -> protocol.Point:
    """Inverse of :func:`point_to_dict` for an already validated mapping."""
    hour, minute = str(data["time"]).split(":")[:2]
    return protocol.Point(
        hour=int(hour),
        minute=int(minute),
        red=int(data["red"]),
        green=int(data["green"]),
        blue=int(data["blue"]),
        white=int(data["white"]),
    )
