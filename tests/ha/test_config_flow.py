"""Config flow, discovery matcher, reauth and options flow against a fake light.

The flow engine, the Bluetooth matcher, the config-entry machinery and the repair/issue
registries are real; only the light (and the scanner list the flows read) is faked.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from bleak.exc import BleakError
from homeassistant.components.bluetooth.match import IntegrationMatcher, ble_device_matches
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_REAUTH, SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.loader import async_get_integration

from custom_components.pawfly import protocol
from custom_components.pawfly.const import DOMAIN

from .conftest import BASE, wait_until
from .fake_light import PROXY_ADAPTER, PROXY_SOURCE, TEST_ADDRESS, FakePawflyLight, make_service_info

OTHER_ADDRESS = "AA:BB:CC:44:55:66"
RIGHT = "12345678"  # the fake light's key
WRONG = "87654321"
LIGHT_ENTITY = f"light.{BASE}"


@pytest.fixture(autouse=True)
async def _unload_entries(hass: HomeAssistant):
    """Flows create entries that connect to the light; release them so nothing lingers."""
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def discovered():
    """What the Bluetooth stack has heard (the user step lists these)."""
    with patch("custom_components.pawfly.config_flow.bluetooth.async_discovered_service_info") as mock:
        mock.return_value = [make_service_info()]
        yield mock


async def _start(hass: HomeAssistant, kind: str, **info_kwargs: Any) -> dict[str, Any]:
    """Open a flow of ``kind`` ('user' or 'bluetooth') for the fake light; returns its first form."""
    if kind == "user":
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["step_id"] == "user"
    else:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info(**info_kwargs)
        )
        assert result["step_id"] == "bluetooth_confirm"
    assert result["type"] is FlowResultType.FORM
    return result


def _input(kind: str, password: str) -> dict[str, Any]:
    return {CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: password} if kind == "user" else {CONF_PASSWORD: password}


async def _submit(hass: HomeAssistant, result: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


def _entries(hass: HomeAssistant) -> list:
    return hass.config_entries.async_entries(DOMAIN)


def _verified_keys(light: FakePawflyLight) -> list[str]:
    return [event[1] for event in light.events_named("verify_key")]


def _reauth_flows(hass: HomeAssistant, entry) -> list[dict[str, Any]]:
    return list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))


KINDS = pytest.mark.parametrize("kind", ["user", "bluetooth"])


# ---------------------------------------------------------------------------------------
# creating an entry
# ---------------------------------------------------------------------------------------


async def test_user_step_creates_entry_after_checking_the_password(hass, light, discovered):
    """A lower-case advert address is normalised; the password is really checked on the light."""
    discovered.return_value = [make_service_info(address=TEST_ADDRESS.lower())]
    form = await _start(hass, "user")
    assert form["errors"] == {}

    result = await _submit(hass, form, {CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: RIGHT})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Pawfly BT-TEST"
    assert result["data"] == {CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: RIGHT}
    (entry,) = _entries(hass)
    assert entry.unique_id == TEST_ADDRESS
    # The check is a short session of its own: subscribe, verify the entered key, let go.
    assert light.events[:4] == [("start_notify",), ("verify_key", RIGHT), ("stop_notify",), ("disconnect",)]
    assert light.disconnections >= 1


async def test_discovery_confirm_creates_entry(hass, light):
    form = await _start(hass, "bluetooth")
    assert hass.config_entries.flow.async_get(form["flow_id"])["context"]["title_placeholders"] == {
        "name": "Pawfly BT-TEST"
    }
    assert form["description_placeholders"] == {"name": "Pawfly BT-TEST"}

    result = await _submit(hass, form, {CONF_PASSWORD: RIGHT})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Pawfly BT-TEST"
    assert result["data"] == {CONF_ADDRESS: TEST_ADDRESS, CONF_PASSWORD: RIGHT}
    assert _entries(hass)[0].unique_id == TEST_ADDRESS
    assert light.events[:4] == [("start_notify",), ("verify_key", RIGHT), ("stop_notify",), ("disconnect",)]


async def test_user_step_offers_only_new_pawfly_lights(hass, make_entry, discovered):
    make_entry()  # TEST_ADDRESS is configured already
    discovered.return_value = [
        make_service_info(),
        make_service_info(address=OTHER_ADDRESS, name="PYLamp-4C-Bar"),
        make_service_info(address="AA:BB:CC:77:88:99", name="Fluval Aquasky"),
    ]

    form = await _start(hass, "user")

    (address_key,) = [key for key in form["data_schema"].schema if str(key) == CONF_ADDRESS]
    options = form["data_schema"].schema[address_key].config["options"]
    assert options == [{"value": OTHER_ADDRESS, "label": f"PYLamp-4C-Bar ({OTHER_ADDRESS})"}]


async def test_user_step_aborts_when_no_light_is_heard(hass, discovered):
    discovered.return_value = []
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_step_aborts_when_every_heard_light_is_configured(hass, make_entry, discovered):
    make_entry()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_step_aborts_when_the_light_was_added_meanwhile(hass, light, discovered):
    """Two open dialogs for one light: the second one to finish must not create a duplicate."""
    first = await _start(hass, "user")
    second = await _start(hass, "user")
    created = await _submit(hass, second, _input("user", RIGHT))
    assert created["type"] is FlowResultType.CREATE_ENTRY

    result = await _submit(hass, first, _input("user", RIGHT))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(_entries(hass)) == 1


async def test_discovery_of_a_configured_light_aborts(hass, make_entry, light):
    make_entry()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert light.events == []


# ---------------------------------------------------------------------------------------
# password and reachability errors
# ---------------------------------------------------------------------------------------


@KINDS
async def test_wrong_password_is_reported_on_the_field_and_retry_works(hass, light, discovered, kind):
    form = await _start(hass, kind)

    result = await _submit(hass, form, _input(kind, WRONG))

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == form["step_id"]
    assert result["errors"] == {CONF_PASSWORD: "invalid_auth"}
    assert _entries(hass) == []

    result = await _submit(hass, result, _input(kind, RIGHT))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] == RIGHT
    assert _verified_keys(light)[:2] == [WRONG, RIGHT]


@KINDS
@pytest.mark.parametrize("password", ["1234567", "123456789", "abcdefgh", "1234 678", ""])
async def test_malformed_password_never_touches_bluetooth(hass, light, discovered, kind, password):
    form = await _start(hass, kind)

    result = await _submit(hass, form, _input(kind, password))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PASSWORD: "invalid_password_format"}
    assert light.events == []
    assert light.connections == 0
    assert _entries(hass) == []


def _unreachable_by_error(light: FakePawflyLight) -> None:
    light.connect_error = BleakError("out of range")


def _unreachable_invisible(light: FakePawflyLight) -> None:
    light.visible = False


def _unreachable_mute(light: FakePawflyLight) -> None:
    light.answer_key = False
    light.silent = True


@KINDS
@pytest.mark.parametrize(
    "break_light", [_unreachable_by_error, _unreachable_invisible, _unreachable_mute], ids=["connect_error", "invisible", "mute"]
)
async def test_unreachable_light_is_a_connection_error_and_recovers(hass, light, discovered, kind, break_light):
    form = await _start(hass, kind)
    break_light(light)

    result = await _submit(hass, form, _input(kind, RIGHT))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert _entries(hass) == []
    # Nothing is left connected after the failed check.
    assert not light.connected

    light.connect_error, light.visible, light.answer_key, light.silent = None, True, True, False
    result = await _submit(hass, result, _input(kind, RIGHT))
    assert result["type"] is FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------------------
# manifest matcher
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "connectable", "expected"),
    [
        ("PY4C-BT-PSTL", True, True),
        ("PYLamp-4C-Foo", True, True),
        ("PYX-Other", True, False),
        ("Fluval Aquasky", True, False),
        (None, True, False),
        ("PY4C-BT-PSTL", False, False),  # a passive scanner cannot open the link
    ],
)
async def test_manifest_matcher(hass, name, connectable, expected):
    """The real light advertises flags + a local name only, so the matcher must be name based."""
    integration = await async_get_integration(hass, DOMAIN)
    matchers = [{**matcher, "domain": DOMAIN} for matcher in integration.manifest["bluetooth"]]
    info = make_service_info(name=name, connectable=connectable)
    assert info.service_uuids == [] and info.manufacturer_data == {}

    assert any(ble_device_matches(matcher, info) for matcher in matchers) is expected

    index = IntegrationMatcher(matchers)
    index.async_setup()
    assert index.match_domains(info) == ({DOMAIN} if expected else set())


# ---------------------------------------------------------------------------------------
# reauth
# ---------------------------------------------------------------------------------------


async def test_reauth_after_setup_with_a_wrong_stored_password(hass, light, setup_entry):
    entry = await setup_entry(password="00000000")
    assert entry.state is ConfigEntryState.SETUP_ERROR
    (flow,) = _reauth_flows(hass, entry)
    assert flow["step_id"] == "reauth_confirm"

    result = await _submit(hass, flow, {CONF_PASSWORD: "11111111"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PASSWORD: "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "00000000"

    result = await _submit(hass, result, {CONF_PASSWORD: RIGHT})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == RIGHT
    assert entry.data[CONF_ADDRESS] == TEST_ADDRESS
    await wait_until(lambda: entry.state is ConfigEntryState.LOADED, message="entry reloaded")
    await wait_until(lambda: hass.states.get(LIGHT_ENTITY).state == "on", message="entities available")
    assert _reauth_flows(hass, entry) == []


async def test_reauth_after_the_password_changes_while_running(hass, light, config_entry):
    entry = config_entry
    assert hass.states.get(LIGHT_ENTITY).state == "on"
    light.clear()
    light.key = WRONG  # changed in the phone app
    light.drop_link()

    await wait_until(lambda: _reauth_flows(hass, entry), message="reauth flow")
    await wait_until(lambda: hass.states.get(LIGHT_ENTITY).state == "unavailable", message="entities unavailable")
    assert entry.state is ConfigEntryState.LOADED

    # Parked, not hammering the light: the single refused attempt is all it ever sends.
    await asyncio.sleep(0.3)  # several reconnect back-off periods of the test timing
    assert _verified_keys(light) == [RIGHT]
    assert light.connections == 2

    (flow,) = _reauth_flows(hass, entry)
    result = await _submit(hass, flow, {CONF_PASSWORD: WRONG})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == WRONG
    await hass.async_block_till_done()
    await wait_until(lambda: hass.states.get(LIGHT_ENTITY).state == "on", message="reconnected")
    assert hass.states.get(f"binary_sensor.{BASE}_connected").state == "on"
    assert entry.state is ConfigEntryState.LOADED
    assert _reauth_flows(hass, entry) == []


# ---------------------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------------------


def _fields(form: dict[str, Any]) -> dict[str, Any]:
    return {str(key): key for key in form["data_schema"].schema}


def _proxy_options(form: dict[str, Any]) -> list[str]:
    schema = form["data_schema"]
    return schema.schema[_fields(form)["preferred_proxy"]].config["options"]


def _scanners(*entries: tuple[str | None, bool]):
    return patch(
        "custom_components.pawfly.config_flow.bluetooth.async_current_scanners",
        return_value=[SimpleNamespace(adapter=adapter, connectable=connectable) for adapter, connectable in entries],
    )


async def test_options_form_defaults_and_offers_only_connectable_proxies(hass, config_entry):
    with _scanners(("proxy-b", True), ("proxy-a", True), ("proxy-passive", False), (None, True)):
        form = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert form["type"] is FlowResultType.FORM
    fields = _fields(form)
    assert {name: key.default() for name, key in fields.items()} == {
        "keep_connected": True,
        "poll_interval": 60,
        "preferred_proxy": "automatic",
    }
    assert _proxy_options(form) == ["automatic", "proxy-a", "proxy-b"]


async def test_a_proxy_chosen_earlier_stays_listed_while_it_is_offline(hass, setup_entry):
    entry = await setup_entry(options={"preferred_proxy": "old-proxy"})
    with _scanners():
        form = await hass.config_entries.options.async_init(entry.entry_id)
    assert _proxy_options(form) == ["automatic", "old-proxy"]
    assert _fields(form)["preferred_proxy"].default() == "old-proxy"


async def test_saving_options_reloads_with_the_new_link_policy(hass, light, config_entry):
    entry = config_entry
    old = entry.runtime_data
    assert old.keep_connected is True
    with _scanners(("proxy-a", True)):
        form = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"keep_connected": False, "poll_interval": 120, "preferred_proxy": "proxy-a"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"keep_connected": False, "poll_interval": 120, "preferred_proxy": "proxy-a"}
    await wait_until(
        lambda: entry.state is ConfigEntryState.LOADED and entry.runtime_data is not old, message="entry reloaded"
    )
    assert entry.runtime_data.keep_connected is False
    assert entry.runtime_data.update_interval == timedelta(seconds=120)


async def test_automatic_is_a_translated_option_the_select_can_return_to(hass, setup_entry):
    """An empty-string option cannot be picked again once a proxy was chosen (the select treats "" as no
    value and keeps the default), so "automatic" is a real, translated value that is stored as empty."""
    entry = await setup_entry(options={"keep_connected": True, "poll_interval": 60, "preferred_proxy": "old-proxy"})
    with _scanners(("old-proxy", True)):
        form = await hass.config_entries.options.async_init(entry.entry_id)
    assert "" not in _proxy_options(form)
    strings = json.loads((Path(protocol.__file__).parent / "strings.json").read_text())
    assert strings["selector"]["preferred_proxy"]["options"] == {"automatic": "Automatic (strongest signal)"}

    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"keep_connected": True, "poll_interval": 60, "preferred_proxy": "automatic"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"keep_connected": True, "poll_interval": 60, "preferred_proxy": ""}
    await wait_until(lambda: entry.state is ConfigEntryState.LOADED, message="entry reloaded")
    with _scanners():
        again = await hass.config_entries.options.async_init(entry.entry_id)
    assert _fields(again)["preferred_proxy"].default() == "automatic"  # and the form shows it again
    hass.config_entries.options.async_abort(again["flow_id"])


async def test_the_proxy_the_integration_remembers_is_a_name_and_never_reloads(hass, light, config_entry):
    """``last_holding_proxy`` is bookkeeping: writing it must not restart the connection."""
    entry = config_entry
    assert entry.data["last_holding_proxy"] == PROXY_ADAPTER
    assert light.address not in entry.data["last_holding_proxy"]
    assert PROXY_SOURCE not in entry.data["last_holding_proxy"]
    coordinator, connections = entry.runtime_data, light.connections

    hass.config_entries.async_update_entry(entry, data={**entry.data, "last_holding_proxy": "some-other-proxy"})
    await hass.async_block_till_done()
    assert entry.runtime_data is coordinator
    assert light.connections == connections

    # A reconnect makes the integration store the real holder again, still without a reload.
    light.drop_link()
    await wait_until(lambda: light.connections == connections + 1, message="reconnect")
    await wait_until(lambda: entry.data["last_holding_proxy"] == PROXY_ADAPTER, message="proxy stored again")
    await hass.async_block_till_done()
    assert entry.runtime_data is coordinator
    assert light.connections == connections + 1
    assert entry.state is ConfigEntryState.LOADED
