"""A scripted Pawfly light on the other end of a fake GATT link (tests/ha only).

Only the light is simulated. Everything the integration does above the wire stays real: the
link supervisor, the serialised GATT lock, the session start-up, the coordinator, the entity
platforms, the services and the config flow.

The fake follows what was verified live on the real light (see ``docs/PROTOCOL.md``):

* set commands (``0xAD``) are never answered, only ``0xBD`` queries reply;
* queries are ignored until the key was verified on the current link, and the key check is
  answered once per new link (a second one on an authenticated link gets no answer);
* the status RGBW is the *manual* colour register: it never changes while a scenario or a
  program runs, white/colour writes flip the mode to manual, brightness never changes mode;
* ``query_program`` for the built-in styles 0-2 gets no reply at all, DIY 1-3 answer a header
  (count 0 when empty) followed by one frame per point.

Every decoded command is appended to ``events`` in wire order so tests can assert the exact
sequence the integration produced.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import SOURCE_LOCAL, BluetoothServiceInfoBleak

from custom_components.pawfly import protocol

#: Obviously synthetic: nothing in this suite may ever reach the real light.
TEST_ADDRESS = "AA:BB:CC:11:22:33"
TEST_NAME = "PY4C-BT-TEST"
DEFAULT_KEY = "12345678"
PROXY_SOURCE = "54:32:04:3E:F3:72"
PROXY_ADAPTER = "plant-room-bluetooth-proxy"


def make_service_info(
    address: str = TEST_ADDRESS,
    name: str | None = TEST_NAME,
    *,
    rssi: int = -55,
    source: str = SOURCE_LOCAL,
    connectable: bool = True,
) -> BluetoothServiceInfoBleak:
    """An advertisement shaped like the real light's: flags + local name, no service UUID."""
    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        rssi=rssi,
        platform_data=((),),
        tx_power=-127,
    )
    return BluetoothServiceInfoBleak(
        name=name or address,
        address=address,
        rssi=rssi,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source=source,
        device=BLEDevice(address=address, name=name, details={}),
        advertisement=advertisement,
        connectable=connectable,
        time=time.monotonic(),
        tx_power=-127,
    )


class FakeChar:
    """The one GATT characteristic (ffe1)."""

    uuid = protocol.CHAR_UUID
    handle = 12

    def __init__(self, properties: tuple[str, ...]) -> None:
        self.properties = list(properties)


class FakeServices:
    def __init__(self, char: FakeChar) -> None:
        self._char = char

    def get_characteristic(self, specifier: Any) -> FakeChar | None:
        return self._char if str(specifier).lower() == protocol.CHAR_UUID else None


class FakeGattClient:
    """The subset of ``BleakClient`` the integration touches."""

    def __init__(self, light: FakePawflyLight) -> None:
        self._light = light
        self.services = FakeServices(light.char)
        self._connected_scanner = SimpleNamespace(
            source=PROXY_SOURCE,
            adapter=PROXY_ADAPTER,
            name=f"{PROXY_ADAPTER} ({PROXY_SOURCE})",
        )

    @property
    def is_connected(self) -> bool:
        return self._light.connected and self._light.client is self

    async def start_notify(self, char: Any, callback: Callable[[Any, bytearray], None], **kwargs: Any) -> None:
        self._light.events.append(("start_notify",))
        self._light.notify_timeouts.append(kwargs.get("timeout"))
        self._light.notify_callback = callback

    async def stop_notify(self, char: Any, **_: Any) -> None:
        self._light.events.append(("stop_notify",))
        self._light.notify_callback = None

    async def write_gatt_char(self, char: Any, data: bytes, response: bool | None = None, **_: Any) -> None:
        self._light.write_responses.append(response)
        await self._light.on_write(bytes(data))

    async def disconnect(self, **_: Any) -> None:
        self._light.events.append(("disconnect",))
        self._light.drop_link(client=self)


class FakePawflyLight:
    """The light's half of the link: applies commands, answers queries, can misbehave."""

    def __init__(self, address: str = TEST_ADDRESS, name: str = TEST_NAME, key: str = DEFAULT_KEY) -> None:
        self.address = address
        self.name = name
        self.key = key

        # -- device state (what the status frame reports) ----------------------------------
        self.power = True
        self.brightness = 90
        self.speed = 0
        self.mode = protocol.Mode.MANUAL
        self.selection = 0  # scenario index, or the wire id of the running program
        self.rgbw = [0, 100, 0, 0]  # the manual colour register, percent
        self.programs: dict[int, list[protocol.Point]] = {3: [], 4: [], 5: []}
        self.clock: tuple[int, int, int, int] | None = None

        # -- how it should behave ------------------------------------------------------------
        self.visible = True  # heard by a scanner (async_ble_device_from_address)
        self.connect_error: Exception | None = None
        self.connect_delay = 0.0
        self.answer_key = True
        self.answer_second_key_check = False
        self.silent = False  # queries go unanswered (a wedged link)
        self.store_programs = True  # False: uploads are silently ignored
        self.apply_key_change = True  # False: change_key is ignored
        self.char = FakeChar(("read", "write-without-response", "write", "notify"))
        self.concatenate_program_reply = False

        # -- what happened ---------------------------------------------------------------------
        self.events: list[tuple[Any, ...]] = []
        self.frames: list[bytes] = []
        self.frame_times: list[float] = []
        self.write_responses: list[bool | None] = []
        self.notify_timeouts: list[float | None] = []  # the backend ``timeout`` each subscribe was given
        self.connections = 0
        self.disconnections = 0
        self.connected = False
        self.client: FakeGattClient | None = None
        self.notify_callback: Callable[[Any, bytearray], None] | None = None
        self.advert_callbacks: list[tuple[Callable[..., None], dict[str, Any]]] = []
        self._disconnected_callback: Callable[[Any], None] | None = None
        self._authed = False
        self._upload: dict[str, Any] | None = None

    # -- link -----------------------------------------------------------------------------------

    def device(self) -> BLEDevice | None:
        return BLEDevice(address=self.address, name=self.name, details={}) if self.visible else None

    def new_client(self, disconnected_callback: Callable[[Any], None] | None) -> FakeGattClient:
        """Stand in for ``bleak_retry_connector.establish_connection``."""
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True
        self.connections += 1
        self._authed = False
        self._upload = None
        self._disconnected_callback = disconnected_callback
        self.notify_callback = None
        self.client = FakeGattClient(self)
        return self.client

    def drop_link(self, client: FakeGattClient | None = None) -> None:
        """The link goes away (the light powered off, the proxy rebooted, we hung up)."""
        if not self.connected:
            return
        self.connected = False
        self.disconnections += 1
        self.notify_callback = None
        callback, self._disconnected_callback = self._disconnected_callback, None
        dropped, self.client = (client or self.client), None
        if callback is not None and dropped is not None:
            callback(dropped)

    def fire_advertisement(self) -> None:
        """Deliver an advertisement to the integration's registered callbacks."""
        info = make_service_info(self.address, self.name)
        for callback, _matcher in list(self.advert_callbacks):
            callback(info, None)

    # -- the wire -----------------------------------------------------------------------------------

    async def on_write(self, data: bytes) -> None:
        self.frames.append(data)
        self.frame_times.append(time.monotonic())
        if len(data) < 4 or protocol.checksum(data[:-1]) != data[-1]:
            self.events.append(("bad_frame", data.hex()))
            return
        family, command, payload = data[0], data[2], data[3:-1]
        if family == protocol.FAMILY_QUERY:
            self._on_query(command, payload)
        elif family == protocol.FAMILY_SET:
            self._on_set(command, payload)
        else:
            self.events.append(("unknown_family", data.hex()))

    def _decode_key(self, payload: bytes) -> str:
        return "".join(f"{byte:02x}" for byte in reversed(payload[0:4]))

    def _on_query(self, command: int, payload: bytes) -> None:
        if command == protocol.QUERY_VERIFY_KEY:
            key = self._decode_key(payload)
            self.events.append(("verify_key", key))
            ok = key == self.key
            already = self._authed
            if ok:
                self._authed = True
            if self.answer_key and (not already or self.answer_second_key_check):
                self.notify(_frame(protocol.FAMILY_QUERY, protocol.REPLY_KEY, bytes((1 if ok else 0,)) + b"\xff" * 4))
        elif command == protocol.QUERY_STATUS:
            self.events.append(("query_status",))
            if self._authed and not self.silent:
                self.notify(self.status_frame())
        elif command == protocol.QUERY_PROGRAM:
            program = protocol.program_index(payload[0])
            self.events.append(("query_program", program))
            if not self._authed or self.silent or program not in self.programs:
                return  # the built-in styles 0-2 are never answered
            points = self.programs[program]
            frames = [
                _frame(protocol.FAMILY_QUERY, protocol.REPLY_PROGRAM_HEADER, bytes((payload[0], len(points), 0, 0x7F)) + b"\xff")
            ]
            for order, point in enumerate(points):
                frames.append(
                    _frame(
                        protocol.FAMILY_QUERY,
                        protocol.REPLY_PROGRAM_POINT,
                        bytes((order, point.hour, point.minute, point.red, point.green, point.blue, point.white)),
                    )
                )
            if self.concatenate_program_reply:
                self.notify(b"".join(frames))
            else:
                for frame in frames:
                    self.notify(frame)
        else:
            self.events.append(("unknown_query", command))

    def _on_set(self, command: int, payload: bytes) -> None:
        if command == protocol.CMD_POWER:
            self.power = bool(payload[0])
            self.events.append(("power", self.power))
        elif command == protocol.CMD_BRIGHTNESS:
            self.brightness = payload[0]
            self.events.append(("brightness", payload[0]))
        elif command == protocol.CMD_SPEED:
            self.speed = payload[0]
            self.events.append(("speed", payload[0]))
        elif command == protocol.CMD_CHANNEL:
            # Opcode 0x04 writes ONE channel (chl 0=R 1=G 2=B 3=W); like colour it flips to manual.
            value, channel = payload[0], payload[1]
            if 0 <= channel <= 3:
                self.rgbw[channel] = value
            self.mode = protocol.Mode.MANUAL
            self.events.append(("white", value, channel) if channel == 3 else ("channel", channel, value))
        elif command == protocol.CMD_COLOR:
            self.rgbw[0:3] = list(payload[0:3])
            self.mode = protocol.Mode.MANUAL
            self.events.append(("color", *payload[0:3]))
        elif command == protocol.CMD_TIME:
            self.clock = (payload[0], payload[1], payload[2], payload[3])
            self.events.append(("time_sync", *self.clock))
        elif command == protocol.CMD_SCENARIO:
            self.mode = protocol.Mode.SCENARIO
            self.selection = payload[0]
            self.events.append(("scenario", payload[0]))
        elif command == protocol.CMD_PROGRAM:
            self.mode = protocol.Mode.PROGRAM
            self.selection = payload[0]
            self.events.append(("program", protocol.program_index(payload[0])))
        elif command == protocol.CMD_PROGRAM_HEADER:
            program, count = protocol.program_index(payload[0]), payload[1]
            self.events.append(("program_header", program, count))
            self._upload = {"program": program, "count": count, "points": []}
            if count == 0:
                self._finish_upload()
        elif command == protocol.CMD_PROGRAM_POINT:
            order, hour, minute, red, green, blue, white = payload[0:7]
            self.events.append(("program_point", order, hour, minute, red, green, blue, white))
            if self._upload is not None:
                self._upload["points"].append(protocol.Point(hour, minute, red, green, blue, white))
                if len(self._upload["points"]) >= self._upload["count"]:
                    self._finish_upload()
        elif command == protocol.CMD_DEMO:
            self.events.append(("demo",))
        elif command == protocol.CMD_PREVIEW:
            self.events.append(("preview", tuple(payload[1:5]) if payload[0] == 1 else None))
        elif command == protocol.CMD_CHANGE_KEY:
            key = self._decode_key(payload)
            self.events.append(("change_key", key))
            if self.apply_key_change:
                self.key = key
        elif command == protocol.CMD_RENAME:
            self.events.append(("rename", bytes(payload).decode()))
        else:
            self.events.append(("unknown_set", command))

    def _finish_upload(self) -> None:
        assert self._upload is not None
        if self.store_programs:
            self.programs[self._upload["program"]] = list(self._upload["points"])
        self._upload = None

    # -- replies ---------------------------------------------------------------------------------------

    def status_frame(self) -> bytes:
        red, green, blue, white = self.rgbw
        return _frame(
            protocol.FAMILY_QUERY,
            protocol.REPLY_STATUS,
            bytes(
                (
                    1 if self.power else 0,
                    self.brightness,
                    self.speed,
                    int(self.mode),
                    self.selection,
                    red,
                    green,
                    blue,
                    white,
                )
            ),
        )

    def notify(self, data: bytes) -> None:
        assert self.notify_callback is not None, "the light replied before notifications were enabled"
        self.notify_callback(self.char, bytearray(data))

    def push_status(self) -> None:
        """An unsolicited status notification (the light changed on its own or via the app)."""
        self.notify(self.status_frame())

    # -- assertions -----------------------------------------------------------------------------------

    def events_named(self, *names: str) -> list[tuple[Any, ...]]:
        return [event for event in self.events if event[0] in names]

    def names(self) -> list[str]:
        return [event[0] for event in self.events]

    def clear(self) -> None:
        self.events.clear()
        self.frames.clear()
        self.frame_times.clear()
        self.write_responses.clear()


def _frame(family: int, command: int, payload: bytes) -> bytes:
    body = bytes((family, len(payload) + 1, command)) + payload
    return body + bytes((protocol.checksum(body),))
