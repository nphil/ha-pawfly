"""Shared fixtures for tests/ha (real Home Assistant, fake light).

The Bluetooth stack is faked at the two places the integration touches it (device lookup /
advert callbacks, and ``establish_connection``); everything above that is the real code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pawfly import coordinator as coordinator_module
from custom_components.pawfly import link as link_module
from custom_components.pawfly.const import DOMAIN

from .fake_light import DEFAULT_KEY, FakePawflyLight

ENTRY_TITLE = "Plant Room Aquarium Accent Light"
BASE = "plant_room_aquarium_accent_light"


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load this repo's ``custom_components/pawfly`` as a real, installed integration."""


@pytest.fixture(autouse=True)
def _prevent_real_bluetooth(mock_bluetooth: None) -> None:
    """``manifest.json`` depends on ``bluetooth_adapters``; never probe real adapters."""


@pytest.fixture
def light() -> FakePawflyLight:
    """A freshly scripted light (key 12345678, on, 90 %, green, manual)."""
    return FakePawflyLight()


#: The production values of the constants ``_fast_timing`` shrinks, for tests that assert an
#: invariant of the real value (for example the frame gap the light's firmware needs).
PRODUCTION_LINK_TIMING = {
    name: getattr(link_module, name) for name in ("FRAME_GAP", "RECONNECT_BACKOFF", "COMMAND_LINK_TIMEOUT")
}


@pytest.fixture(autouse=True)
def _fast_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real behaviour, test-sized clocks: nothing waits for a production-sized delay."""
    for name, value in {
        "FRAME_GAP": 0.0,
        "CHANGE_KEY_SETTLE": 0.0,
        "RECONNECT_BACKOFF": (0.02, 0.04, 0.06),
        "RECONNECT_BACKOFF_JITTER": 0.0,
        "DROP_RETRY_DELAY": 0.02,
        "KICK_MIN_GAP": 0.0,
        "ADVERT_KICK_MIN_GAP": 0.0,
        "KEY_REPLY_TIMEOUT": 0.3,
        "STATUS_REPLY_TIMEOUT": 0.4,
        "PROGRAM_REPLY_TIMEOUT": 0.4,
        "COMMAND_LINK_TIMEOUT": 1.5,
        "ON_DEMAND_LINGER": 0.6,
        "ON_DEMAND_REQUEST_WINDOW": 1.5,
        "RELINK_TIMEOUT": 3.0,
        "STOP_GRACE": 2.0,
    }.items():
        monkeypatch.setattr(link_module, name, value)
    monkeypatch.setattr(coordinator_module, "STATUS_DEBOUNCE", 0.01)
    monkeypatch.setattr(coordinator_module, "PROGRAM_SETTLE", 0.0)
    monkeypatch.setattr(coordinator_module, "FIRST_SESSION_TIMEOUT", 1.5)
    monkeypatch.setattr(coordinator_module, "STATUS_LINK_TIMEOUT", 1.0)
    monkeypatch.setattr(coordinator_module, "PREVIEW_LINK_TIMEOUT", 0.5)
    monkeypatch.setattr(coordinator_module, "ON_DEMAND_REQUEST_WINDOW", 1.5)


@pytest.fixture(autouse=True)
def _fake_bluetooth(light: FakePawflyLight):
    """Route device lookup, advert callbacks and GATT connects to ``light``."""

    def _device_from_address(hass: Any, address: str, connectable: bool = True) -> Any:
        return light.device() if address.upper() == light.address.upper() else None

    async def _establish_connection(
        client_class: Any, device: Any, name: str, disconnected_callback: Any = None, **kwargs: Any
    ) -> Any:
        if light.connect_delay:
            await asyncio.sleep(light.connect_delay)
        return light.new_client(disconnected_callback)

    def _register_callback(hass: Any, callback: Any, matcher: Any, mode: Any) -> Callable[[], None]:
        entry = (callback, dict(matcher))
        light.advert_callbacks.append(entry)
        return lambda: light.advert_callbacks.remove(entry) if entry in light.advert_callbacks else None

    with (
        patch("custom_components.pawfly.link.establish_connection", side_effect=_establish_connection),
        patch(
            "custom_components.pawfly.link.bluetooth.async_ble_device_from_address",
            side_effect=_device_from_address,
        ),
        patch("custom_components.pawfly.link.bluetooth.async_register_callback", side_effect=_register_callback),
    ):
        yield


@pytest.fixture
def make_entry(hass: HomeAssistant, light: FakePawflyLight) -> Callable[..., MockConfigEntry]:
    """Factory for a config entry pointing at ``light`` (added to hass, not set up)."""

    def _make(
        *,
        password: str = DEFAULT_KEY,
        options: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        title: str = ENTRY_TITLE,
    ) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=title,
            unique_id=light.address,
            data={CONF_ADDRESS: light.address, CONF_PASSWORD: password, **(data or {})},
            options=options or {},
        )
        entry.add_to_hass(hass)
        return entry

    return _make


@pytest.fixture
async def setup_entry(hass: HomeAssistant, make_entry: Callable[..., MockConfigEntry]):
    """Factory that sets an entry up through real HA machinery and unloads it at teardown."""
    created: list[MockConfigEntry] = []

    async def _setup(**kwargs: Any) -> MockConfigEntry:
        entry = make_entry(**kwargs)
        created.append(entry)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    yield _setup
    for entry in created:
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def config_entry(setup_entry: Callable[..., Awaitable[MockConfigEntry]]) -> MockConfigEntry:
    """A loaded entry for the default light."""
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def wait_until(predicate: Callable[[], bool], *, timeout: float = 3.0, message: str = "condition") -> None:
    """Poll until ``predicate()`` holds (the link and its debounces run on real, short timers)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for {message}")
        await asyncio.sleep(0.01)
