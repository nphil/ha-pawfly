"""Wire protocol of the Pawfly / PinYing PY4C Bluetooth aquarium light.

Pure Python, no Home Assistant imports: every function here turns arguments into
the exact bytes the vendor app ("P-light controller", ``com.aquapinyin.control``)
writes to the light, or turns bytes notified by the light into small dataclasses.

Everything is derived from the decompiled vendor app (``research/decompiled``,
mainly ``AquaPinyin.Services/FourChannel.cs`` and ``BaseDevice.cs``) and checked
against a real light; ``docs/PROTOCOL.md`` lists what was verified live.

Frame layout (both directions)::

    [family, length, command, payload..., checksum]

* ``family`` is ``0xAD`` for commands that change something and ``0xBD`` for
  queries and authentication. Every reply from the light starts with ``0xBD``.
* ``length`` is ``len(payload) + 1`` (the command byte plus the payload).
* ``checksum`` is the sum of every previous byte modulo 256.
* Unused payload bytes are ``0xFF`` (a few frames use ``0x00``; see the functions).

Things to know before talking to the light (all seen on the real device):

* There is a single GATT characteristic (``ffe1``) for both writing and notifying.
* After every new connection the light ignores queries until :func:`verify_key` succeeded.
* ``0xAD`` commands are never acknowledged; ask with :func:`query_status` to confirm them.
* The light's controller sits behind a UART: frames sent closer together than
  :data:`FRAME_GAP_MIN_S` are silently dropped, so pace every frame (program uploads
  especially) by :data:`FRAME_GAP_S` and read a program back to verify it.
* The colour fields of the status reply are the *manual colour register*. They do not follow
  scenarios or programs, so they never show what the light is really doing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

FAMILY_SET = 0xAD
FAMILY_QUERY = 0xBD

_FILL = 0xFF

# Command bytes -------------------------------------------------------------------------
CMD_POWER = 0x01
CMD_BRIGHTNESS = 0x02
CMD_SPEED = 0x03
CMD_CHANNEL = 0x04
CMD_COLOR = 0x05
CMD_TIME = 0x06
CMD_SCENARIO = 0x07
CMD_PROGRAM = 0x08
CMD_PROGRAM_HEADER = 0x09
CMD_PROGRAM_POINT = 0x0A
CMD_DEMO = 0x0B
CMD_PREVIEW = 0x0C
CMD_CHANGE_KEY = 0x20
CMD_RENAME = 0x21

QUERY_STATUS = 0x01
QUERY_PROGRAM = 0x02
QUERY_VERIFY_KEY = 0x0A

REPLY_STATUS = 0x81
REPLY_PROGRAM_HEADER = 0x82
REPLY_PROGRAM_POINT = 0x83
REPLY_KEY = 0x8A

#: The light addresses DIY programs as ``program + 125`` (128, 129, 130).
DIY_ID_OFFSET = 125
#: Program index of the first DIY slot (0-2 are the built-in styles).
FIRST_DIY_PROGRAM = 3
LAST_PROGRAM = 5

PROGRAM_NAMES = ("Japanese style", "Dutch style", "Jungle style", "DIY 1", "DIY 2", "DIY 3")

#: Scenario (built-in animated effect) names in the order of the app's buttons and of the
#: firmware's scenario index. The last four are the app's own English labels
#: (``AppResources``: Warm White, Bright White, Day, RGB Cycle). The first four buttons are
#: icon-only in the app (sc1..sc4.png: partly cloudy, thunderstorm, sun, crescent moon) and
#: have no text anywhere in the app, so these four names describe the icons.
SCENARIO_NAMES = (
    "Cloudy",
    "Thunderstorm",
    "Sunny",
    "Moonlight",
    "Warm White",
    "Bright White",
    "Day",
    "RGB Cycle",
)

#: The colour the app *displays* on its palette after choosing a scenario (``AdjScenario``):
#: (red, green, blue, white) in percent. It is a mirror kept by the app, not a device value.
SCENARIO_COLORS: tuple[tuple[int, int, int, int], ...] = (
    (30, 30, 30, 30),
    (0, 0, 0, 0),
    (100, 100, 100, 100),
    (0, 10, 100, 0),
    (100, 0, 0, 100),
    (0, 0, 100, 100),
    (0, 0, 0, 100),
    (100, 100, 100, 0),
)

# Ranges enforced by the encoders (percent, unless noted) -----------------------------
#: The app's brightness slider runs 1..100 (Lamp4CPage.cs:857-858). The firmware also accepts 0
#: (fully dark while "on", checked with a camera), so :func:`brightness` takes 0..100.
BRIGHTNESS_MIN = 1
BRIGHTNESS_MAX = 100
COLOR_MAX = 100
#: The speed frame (0x03) exists in the app's code but no page ever sends it, and on the real
#: light it is a no-op: the value is not stored (status.speed stays 0) and scenario animation
#: does not change. Do not offer a speed control.
SPEED_SUPPORTED = False
SPEED_MIN = 0
SPEED_MAX = 255  # only the width of the wire byte
MAX_PROGRAM_POINTS = 12  # "Up to 12 timers can be set." (Lamp4CTimersPage.cs:691,705)
#: Pacing between frames, measured with 12-point uploads: 1 ms lost 4 of 12 points, 10 ms lost 1,
#: 15 ms lost points in 2 of 4 runs, 20 ms and up lost nothing (4/4 at 20, 30, 40, 80 ms).
FRAME_GAP_MIN_S = 0.02
FRAME_GAP_S = 0.08  # recommended: four times the smallest gap that worked
KEY_LENGTH = 8
NAME_MAX_LENGTH = 16  # characters/bytes (BaseDevice.cs:46, 135-139)
SCENARIO_COUNT = len(SCENARIO_NAMES)

#: Channel byte of opcode 0x04: 0 red, 1 green, 2 blue, 3 white (0-2 are not used by the app).
CHANNEL_RED = 0
CHANNEL_GREEN = 1
CHANNEL_BLUE = 2
CHANNEL_WHITE = 3
CHANNEL_NAMES = ("red", "green", "blue", "white")

#: Bluetooth name prefixes the app scans for (``BaseDevice.AdvertisedName``); the app shows and
#: renames the part after the prefix and any ``-``/``_`` that follows it.
ADVERTISED_PREFIXES = ("PY4C", "PYLamp-4C")

_KEY_RE = re.compile(r"[0-9]{8}")

# --------------------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------------------


class Mode(IntEnum):
    """Working mode reported in the status frame."""

    MANUAL = 0
    SCENARIO = 1
    PROGRAM = 2


@dataclass(frozen=True)
class Point:
    """One time point of a program: from ``hour:minute`` on, aim for this colour (percent)."""

    hour: int
    minute: int
    red: int
    green: int
    blue: int
    white: int

    @property
    def minute_of_day(self) -> int:
        return self.hour * 60 + self.minute


@dataclass(frozen=True)
class Status:
    """Reply to :func:`query_status`."""

    power: bool
    brightness: int
    speed: int
    mode: Mode
    scenario: int | None
    program: int | None
    red: int
    green: int
    blue: int
    white: int


@dataclass(frozen=True)
class KeyResult:
    """Reply to :func:`verify_key`: ``ok`` is False when the light refused the key."""

    ok: bool


@dataclass(frozen=True)
class ProgramHeader:
    """First reply to :func:`query_program`: how many points follow."""

    program: int
    count: int


@dataclass(frozen=True)
class ProgramPoint:
    """One ``count`` reply of :func:`query_program`; ``order`` counts from 0."""

    order: int
    point: Point


# --------------------------------------------------------------------------------------
# Encoders
# --------------------------------------------------------------------------------------


def checksum(data: bytes) -> int:
    """The vendor's checksum: running sum modulo 65535 (``BaseDevice.CheckSum``), low byte."""
    total = 0
    for byte in data:
        total = (total + byte) % 65535
    return total & 0xFF


def _frame(family: int, command: int, payload: bytes) -> bytes:
    body = bytes((family, len(payload) + 1, command)) + payload
    return body + bytes((checksum(body),))


def _fixed(family: int, command: int, *args: int) -> bytes:
    """Frame with a 5-byte payload: ``args`` first, then ``0xFF`` fill."""
    return _frame(family, command, bytes(args) + bytes((_FILL,)) * (5 - len(args)))


def _check_range(name: str, value: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}, got {value}")
    return value


def _pack_key(key: str) -> bytes:
    """8 digits ``k0..k7`` are sent as the four bytes ``k6k7 k4k5 k2k3 k0k1`` (BCD-like)."""
    if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
        raise ValueError("the key must be exactly 8 digits (0-9)")
    return bytes.fromhex(key[6:8] + key[4:6] + key[2:4] + key[0:2])


def verify_key(key: str) -> bytes:
    """Authenticate. The light ignores every query until this succeeded (reply: KeyResult)."""
    return _frame(FAMILY_QUERY, QUERY_VERIFY_KEY, _pack_key(key) + bytes((_FILL,)))


def change_key(key: str) -> bytes:
    """Set a new 8-digit key. The app only offers this after a successful :func:`verify_key`."""
    return _frame(FAMILY_SET, CMD_CHANGE_KEY, _pack_key(key) + bytes((_FILL,)))


def display_name(advertised_name: str) -> str:
    """The name the app shows: the advertised name without its ``PY4C``/``PYLamp-4C`` prefix.

    Mirrors ``BaseDevice.DisplayName``: the prefix and the ``-``/``_`` after it are dropped, but a
    name that is only the prefix is kept as it is.
    """
    for prefix in ADVERTISED_PREFIXES:
        if advertised_name.startswith(prefix):
            if len(advertised_name) > len(prefix):
                return advertised_name[len(prefix) :].lstrip("-_")
            return advertised_name
    return advertised_name


def rename(name: str) -> bytes:
    """Rename the light.

    ``name`` is the display name, without the ``PY4C`` prefix: the app sends exactly that and
    scans for the prefix again afterwards, so the firmware keeps the prefix itself. A typed
    ``PY4C-``/``PYLamp-4C-`` prefix is removed like the app's own display name. The name must be
    1..16 printable ASCII characters, otherwise ``ValueError``.
    """
    if not isinstance(name, str):
        raise TypeError(f"name must be a str, got {type(name).__name__}")
    name = display_name(name)
    if not name.strip():
        raise ValueError("the name must not be empty")
    if len(name) > NAME_MAX_LENGTH:
        raise ValueError(f"the name must be at most {NAME_MAX_LENGTH} characters, got {len(name)}")
    if not all(" " <= ch <= "~" for ch in name):
        raise ValueError("the name may only contain printable ASCII characters")
    return _frame(FAMILY_SET, CMD_RENAME, name.encode("ascii"))


def query_status() -> bytes:
    return _fixed(FAMILY_QUERY, QUERY_STATUS)


def program_id(program: int) -> int:
    """Wire id of a program index (0-5): 0-2 as is, DIY 1-3 (3-5) become 128-130."""
    _check_range("program", program, 0, LAST_PROGRAM)
    return program if program < FIRST_DIY_PROGRAM else program + DIY_ID_OFFSET


def program_index(wire_id: int) -> int:
    """Inverse of :func:`program_id` (the app's ``>= 128 -> -125`` rule)."""
    return wire_id - DIY_ID_OFFSET if wire_id >= 128 else wire_id


def query_program(program: int) -> bytes:
    return _fixed(FAMILY_QUERY, QUERY_PROGRAM, program_id(program))


def power(on: bool) -> bytes:
    return _fixed(FAMILY_SET, CMD_POWER, 1 if on else 0)


def brightness(pct: int) -> bytes:
    """Overall brightness 0..100 (the app's slider starts at 1; 0 is fully dark but still "on")."""
    return _fixed(FAMILY_SET, CMD_BRIGHTNESS, _check_range("brightness", pct, 0, BRIGHTNESS_MAX))


def speed(v: int) -> bytes:
    """Effect speed frame. A no-op on the real light (see :data:`SPEED_SUPPORTED`)."""
    return _fixed(FAMILY_SET, CMD_SPEED, _check_range("speed", v, SPEED_MIN, SPEED_MAX))


def channel(chl: int, value: int) -> bytes:
    """Set ONE output channel (0 red, 1 green, 2 blue, 3 white) to 0..100, leaving the others alone.

    The app only uses channel 3 (white); 0-2 were found by testing the real light. Like
    :func:`color` and :func:`white` it puts the light into manual mode.
    """
    _check_range("channel", chl, CHANNEL_RED, CHANNEL_WHITE)
    return _fixed(FAMILY_SET, CMD_CHANNEL, _check_range(CHANNEL_NAMES[chl], value, 0, COLOR_MAX), chl)


def white(w: int) -> bytes:
    """White channel, 0-100 (opcode 0x04, channel byte 3)."""
    return channel(CHANNEL_WHITE, w)


def color(r: int, g: int, b: int) -> bytes:
    return _fixed(
        FAMILY_SET,
        CMD_COLOR,
        _check_range("red", r, 0, COLOR_MAX),
        _check_range("green", g, 0, COLOR_MAX),
        _check_range("blue", b, 0, COLOR_MAX),
    )


def scenario(idx: int) -> bytes:
    return _fixed(FAMILY_SET, CMD_SCENARIO, _check_range("scenario", idx, 0, SCENARIO_COUNT - 1))


def program(idx: int) -> bytes:
    """Run a stored program (0-2 built-in styles, 3-5 DIY 1-3)."""
    return _fixed(FAMILY_SET, CMD_PROGRAM, program_id(idx))


def demo() -> bytes:
    """The app's DEMO button (only offered while a program runs)."""
    return _fixed(FAMILY_SET, CMD_DEMO)


def time_sync(dt: datetime) -> bytes:
    """Set the light's clock (local time). Weekday is Monday=1 .. Sunday=7."""
    return _fixed(FAMILY_SET, CMD_TIME, dt.hour, dt.minute, dt.second, dt.isoweekday())


def program_upload(idx: int, points: Sequence[Point]) -> list[bytes]:
    """Frames that replace DIY program ``idx`` (3-5): one header, then one frame per point.

    Points are sent sorted by time with ``order`` counting from 0, like the app. An empty
    sequence clears the program. Frames must be written in order, waiting for each write.
    """
    _check_range("program", idx, FIRST_DIY_PROGRAM, LAST_PROGRAM)
    points = sorted(points, key=lambda p: (p.hour, p.minute))
    if len(points) > MAX_PROGRAM_POINTS:
        raise ValueError(f"a program holds at most {MAX_PROGRAM_POINTS} points, got {len(points)}")
    frames = [_fixed(FAMILY_SET, CMD_PROGRAM_HEADER, program_id(idx), len(points), 0x00, 0x7F)]
    seen: set[tuple[int, int]] = set()
    for order, p in enumerate(points):
        _check_range("hour", p.hour, 0, 23)
        _check_range("minute", p.minute, 0, 59)
        if (p.hour, p.minute) in seen:
            raise ValueError(f"two points at {p.hour:02d}:{p.minute:02d}")
        seen.add((p.hour, p.minute))
        frames.append(
            _frame(
                FAMILY_SET,
                CMD_PROGRAM_POINT,
                bytes(
                    (
                        order,
                        p.hour,
                        p.minute,
                        _check_range("red", p.red, 0, COLOR_MAX),
                        _check_range("green", p.green, 0, COLOR_MAX),
                        _check_range("blue", p.blue, 0, COLOR_MAX),
                        _check_range("white", p.white, 0, COLOR_MAX),
                    )
                ),
            )
        )
    return frames


def preview(rgbw: tuple[int, int, int, int] | None) -> bytes:
    """Show a colour immediately without changing mode (DIY editor); ``None`` ends the preview."""
    if rgbw is None:
        return _frame(FAMILY_SET, CMD_PREVIEW, bytes(5))
    r, g, b, w = rgbw
    return _frame(
        FAMILY_SET,
        CMD_PREVIEW,
        bytes(
            (
                1,
                _check_range("red", r, 0, COLOR_MAX),
                _check_range("green", g, 0, COLOR_MAX),
                _check_range("blue", b, 0, COLOR_MAX),
                _check_range("white", w, 0, COLOR_MAX),
            )
        ),
    )


# --------------------------------------------------------------------------------------
# Decoders
# --------------------------------------------------------------------------------------


def _first_frame(data: bytes) -> bytes | None:
    """The first complete, checksum-correct ``0xBD`` frame in ``data`` (the app also scans for 0xBD)."""
    start = data.find(FAMILY_QUERY)
    if start < 0 or len(data) - start < 4:
        return None
    end = start + data[start + 1] + 3
    if end > len(data):
        return None
    frame = data[start:end]
    if checksum(frame[:-1]) != frame[-1]:
        return None
    return frame


def parse(data: bytes) -> Status | KeyResult | ProgramHeader | ProgramPoint | None:
    """Decode one notification from the light; ``None`` for anything not understood."""
    frame = _first_frame(bytes(data))
    if frame is None:
        return None
    cmd = frame[2]
    if cmd == REPLY_STATUS and len(frame) >= 13:
        mode_raw, sel = frame[6], frame[7]
        # like the app: anything that is not scenario (1) or program (2) counts as manual
        mode = Mode(mode_raw) if mode_raw in (Mode.SCENARIO, Mode.PROGRAM) else Mode.MANUAL
        scenario_sel = sel if mode is Mode.SCENARIO and sel < SCENARIO_COUNT else None
        program_sel = program_index(sel) if mode is Mode.PROGRAM else None
        return Status(
            power=frame[3] != 0,
            brightness=frame[4],
            speed=frame[5],
            mode=mode,
            scenario=scenario_sel,
            program=program_sel if program_sel is not None and 0 <= program_sel <= LAST_PROGRAM else None,
            red=frame[8],
            green=frame[9],
            blue=frame[10],
            white=frame[11],
        )
    if cmd == REPLY_KEY and len(frame) >= 5:
        return KeyResult(ok=frame[3] == 1)
    if cmd == REPLY_PROGRAM_HEADER and len(frame) >= 9:
        if program_id(FIRST_DIY_PROGRAM) <= frame[3] <= program_id(LAST_PROGRAM):  # only DIY slots answer
            return ProgramHeader(program=program_index(frame[3]), count=frame[4])
        return None
    if cmd == REPLY_PROGRAM_POINT and len(frame) >= 11:
        if frame[4] > 23 or frame[5] > 59:  # the app's TimeOnly would throw on these
            return None
        return ProgramPoint(
            order=frame[3],
            point=Point(hour=frame[4], minute=frame[5], red=frame[6], green=frame[7], blue=frame[8], white=frame[9]),
        )
    return None


class ProgramAssembler:
    """Collects a program query's replies: feed every :func:`parse` result in arrival order.

    :meth:`feed` returns ``(program, points)`` once the header's announced number of points
    arrived (immediately for an empty program), else ``None``. A new header restarts collection.
    """

    def __init__(self) -> None:
        self._program: int | None = None
        self._count = 0
        self._points: list[ProgramPoint] = []

    def feed(self, msg: object) -> tuple[int, list[Point]] | None:
        if isinstance(msg, ProgramHeader):
            self._program = msg.program
            self._count = msg.count
            self._points = []
            return self._finish() if msg.count == 0 else None
        if isinstance(msg, ProgramPoint) and self._program is not None:
            self._points.append(msg)
            if len(self._points) >= self._count:
                return self._finish()
        return None

    def _finish(self) -> tuple[int, list[Point]]:
        assert self._program is not None
        result = (self._program, [m.point for m in sorted(self._points, key=lambda m: m.order)])
        self._program = None
        self._count = 0
        self._points = []
        return result


def split_frames(data: bytes) -> list[bytes]:
    """Split a notification that carries several frames back to back."""
    out: list[bytes] = []
    i = 0
    while i + 2 < len(data):
        end = i + data[i + 1] + 3
        if end > len(data):
            break
        out.append(bytes(data[i:end]))
        i = end
    return out


def _points(*rows: Iterable[int]) -> tuple[Point, ...]:
    return tuple(Point(*row) for row in rows)  # type: ignore[arg-type]


#: The three built-in programs as the app draws them (``Lamp4CTimer.Preset``); the light
#: does not answer a query for programs 0-2, the app keeps these curves itself.
PRESETS: dict[int, tuple[Point, ...]] = {
    0: _points(
        (7, 0, 0, 0, 0, 0),
        (7, 15, 70, 70, 70, 70),
        (8, 0, 70, 70, 70, 70),
        (8, 15, 100, 100, 100, 100),
        (15, 0, 100, 100, 100, 100),
        (15, 15, 70, 70, 70, 70),
        (18, 0, 70, 70, 70, 70),
        (18, 15, 0, 0, 100, 0),
        (22, 0, 0, 0, 100, 0),
        (22, 15, 0, 0, 0, 0),
    ),
    1: _points(
        (6, 0, 0, 0, 0, 0),
        (8, 0, 50, 12, 3, 15),
        (9, 0, 65, 30, 20, 50),
        (11, 0, 100, 0, 0, 100),
        (13, 0, 100, 100, 100, 100),
        (16, 0, 70, 70, 100, 100),
        (17, 0, 50, 50, 50, 50),
        (18, 0, 50, 10, 10, 10),
        (19, 0, 0, 50, 100, 0),
        (22, 0, 0, 0, 0, 0),
    ),
    2: _points(
        (8, 0, 0, 0, 0, 0),
        (8, 30, 100, 50, 10, 10),
        (9, 0, 100, 100, 100, 100),
        (17, 0, 100, 100, 100, 100),
        (18, 0, 100, 50, 15, 0),
        (19, 0, 0, 50, 100, 0),
        (22, 0, 0, 0, 0, 0),
    ),
}
