"""Diagnostics, translations and the shipped brand files."""

from __future__ import annotations

import json
import logging
import re
import struct
from pathlib import Path

import pytest
from homeassistant.const import CONF_PASSWORD
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry

from custom_components.pawfly.const import DOMAIN

from .conftest import BASE
from .fake_light import DEFAULT_KEY, TEST_ADDRESS

COMPONENT = Path(__file__).resolve().parents[2] / "custom_components" / "pawfly"


async def test_diagnostics_redacts_the_password_and_shows_the_link(hass, hass_client, config_entry) -> None:
    """The download carries what a link problem needs (address, proxy, link state, last
    status) and never the password."""
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert diagnostics["entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert DEFAULT_KEY not in json.dumps(diagnostics)
    assert diagnostics["entry"]["data"]["address"] == TEST_ADDRESS
    assert diagnostics["entry"]["state"] == "loaded"
    coordinator = diagnostics["coordinator"]
    assert coordinator["link"]["state"] == "ready"
    assert coordinator["link"]["route_adapter"] == "plant-room-bluetooth-proxy"
    assert coordinator["status"]["brightness"] == 90
    assert coordinator["status"]["mode"] == "MANUAL"
    assert diagnostics["outage_seconds"] is None


def test_translations_file_is_the_strings_file() -> None:
    """Home Assistant reads ``translations/en.json``; it must be exactly ``strings.json``."""
    assert (COMPONENT / "translations" / "en.json").read_bytes() == (COMPONENT / "strings.json").read_bytes()


def test_every_action_is_described_and_has_an_icon() -> None:
    """An action without a name/description/icon shows up blank in the automation editor."""
    strings = json.loads((COMPONENT / "strings.json").read_text())
    icons = json.loads((COMPONENT / "icons.json").read_text())
    yaml_services = set(re.findall(r"^([a-z_]+):\s*$", (COMPONENT / "services.yaml").read_text(), re.M))

    assert yaml_services == {
        "get_program",
        "set_program",
        "set_channel",
        "preview",
        "sync_time",
        "rename",
        "change_password",
        "release_link",
    }
    assert set(strings["services"]) == yaml_services
    assert set(icons["services"]) == yaml_services
    for name, service in strings["services"].items():
        assert service["name"] and service["description"], name
        for field, text in service["fields"].items():
            assert text["name"] and text["description"], f"{name}.{field}"


@pytest.mark.parametrize(
    ("key", "placeholders"),
    [
        ("not_connected", {"name": "Tank", "error": "boom"}),
        ("command_failed", {"name": "Tank", "error": "boom"}),
        ("auth_failed", {"name": "Tank"}),
        ("not_ready", {"name": "Tank", "error": "boom"}),
        ("password_not_accepted", {"name": "Tank"}),
        ("program_readback_mismatch", {"name": "Tank", "program": "DIY 1", "expected": "3", "found": "2"}),
        ("program_read_only", {"program": "Japanese style"}),
        ("release_failed", {"names": "Tank"}),
        ("entry_not_loaded", {"name": "Tank"}),
        ("demo_needs_program", {"name": "Tank"}),
        ("unknown_effect", {"effect": "Nope"}),
        ("invalid_points", {"error": "two points at 07:00"}),
        ("invalid_name", {"error": "too long"}),
        ("invalid_password", {"error": "8 digits"}),
        ("device_not_found", {"device_id": "abc"}),
        ("not_a_pawfly_device", {"device_id": "abc"}),
    ],
)
async def test_error_messages_render_with_the_placeholders_the_code_passes(hass, key, placeholders) -> None:
    """Each translated message exists and is complete with exactly the placeholders the
    integration passes (a typo'd key or placeholder would show users a raw key or a KeyError)."""
    translations = await async_get_translations(hass, "en", "exceptions", [DOMAIN])
    message = translations[f"component.{DOMAIN}.exceptions.{key}.message"]
    rendered = message.format(**placeholders)
    assert "{" not in rendered
    assert all(value in rendered for value in placeholders.values())


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("icon.png", (256, 256)),
        ("icon@2x.png", (512, 512)),
        ("dark_icon.png", (256, 256)),
        ("dark_icon@2x.png", (512, 512)),
        ("logo.png", (864, 256)),
        ("logo@2x.png", (1728, 512)),
        ("dark_logo.png", (864, 256)),
        ("dark_logo@2x.png", (1728, 512)),
    ],
)
def test_brand_images_exist_with_the_sizes_core_serves(name: str, size: tuple[int, int]) -> None:
    """Core serves ``custom_components/<domain>/brand/`` directly; these are its file names."""
    assert _png_size(COMPONENT / "brand" / name) == size


async def test_entity_ids_follow_the_device_name(hass, config_entry) -> None:
    """The main light IS the device (no own name), the rest read '<device> <thing>'."""
    assert hass.states.get(f"light.{BASE}").name == "Plant Room Aquarium Accent Light"
    assert hass.states.get(f"select.{BASE}_program").name == "Plant Room Aquarium Accent Light Program"


async def test_the_password_never_reaches_the_debug_log(hass, setup_entry, light, caplog) -> None:
    """Key frames carry the password (packed as BCD-like hex); debug logs get shared in bug reports."""
    caplog.set_level(logging.DEBUG, logger="custom_components.pawfly")
    light.key = "24681357"
    entry = await setup_entry(password="24681357")
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, TEST_ADDRESS), config_entry_id=entry.entry_id)

    await hass.services.async_call(
        DOMAIN, "change_password", {"device_id": device.id, "new_password": "13572468"}, blocking=True
    )

    assert entry.data[CONF_PASSWORD] == "13572468"  # the change really went through the key frames
    logged = caplog.text.lower()
    assert "wrote" in logged  # the session was really logged at debug level
    for secret in ("24681357", "13572468", "57136824", "68245713"):  # digits and packed frame bytes
        assert secret not in logged
