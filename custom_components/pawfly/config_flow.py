"""Config flow of the Pawfly Aquarium Light.

Discovery comes from the manifest's Bluetooth matcher: the light advertises only a local
name (``PY4C-...`` or ``PYLamp-4C-...``, no service UUID), so the matcher is name based. The
user step lists lights the Bluetooth stack has heard for setups where discovery has not
fired. Both paths ask for the 8-digit password (default ``12345678``) and check it against
the light over Bluetooth before an entry exists; a wrong password is reported on the field.
``unique_id`` is the BLE address, so a discovered and a hand-picked light can never become
two entries. A password the light stops accepting later starts the reauth flow.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_KEEP_CONNECTED,
    CONF_POLL_INTERVAL,
    CONF_PREFERRED_PROXY,
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_PASSWORD,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    PASSWORD_LENGTH,
    PROXY_AUTOMATIC,
)
from .link import AuthFailed, NotConnected, async_check_password
from .model import display_name, is_pawfly_name

_LOGGER = logging.getLogger(__name__)

_PASSWORD_RE = re.compile(rf"[0-9]{{{PASSWORD_LENGTH}}}")
_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _title(local_name: str | None) -> str:
    """Entry title: ``Pawfly <name shown by the vendor app>``."""
    shown = display_name(local_name)
    return f"Pawfly {shown}" if shown else "Pawfly Aquarium Light"


def _stored_proxy(chosen: str | None) -> str:
    """The proxy to store: nothing for "automatic" (or an emptied custom value)."""
    return "" if chosen in (None, "", PROXY_AUTOMATIC) else chosen


def _password_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_PASSWORD, default=DEFAULT_PASSWORD): _PASSWORD_SELECTOR})


class PawflyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a light from Bluetooth discovery or by picking one."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: bluetooth.BluetoothServiceInfoBleak | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PawflyOptionsFlow:
        return PawflyOptionsFlow()

    async def _async_verify(self, address: str, password: str, name: str) -> dict[str, str]:
        """Ask the light whether it accepts ``password``: ``{}`` when it does, else form errors."""
        if _PASSWORD_RE.fullmatch(password) is None:
            return {CONF_PASSWORD: "invalid_password_format"}
        try:
            await async_check_password(self.hass, address, password, name=name)
        except AuthFailed:
            return {CONF_PASSWORD: "invalid_auth"}
        except NotConnected as err:
            _LOGGER.debug("Cannot check the password of %s: %s", address, err)
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001 - a flow must show an error, never crash
            _LOGGER.exception("Unexpected error while checking the password of %s", address)
            return {"base": "unknown"}
        return {}

    def _discovered_lights(self) -> dict[str, str]:
        """``{address: label}`` of lights heard by any adapter or proxy and not yet configured."""
        configured = self._async_current_ids()
        found: dict[str, str] = {}
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            address = info.address.upper()
            if is_pawfly_name(info.name) and address not in configured:
                found[address] = f"{info.name} ({address})"
        return found

    async def async_step_bluetooth(self, discovery_info: bluetooth.BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """A light started advertising: confirm it (and its password) before adding it."""
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": _title(discovery_info.name)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        discovery = self._discovery
        assert discovery is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_verify(discovery.address, user_input[CONF_PASSWORD], discovery.name)
            if not errors:
                return self.async_create_entry(
                    title=_title(discovery.name),
                    data={CONF_ADDRESS: discovery.address.upper(), CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=self.add_suggested_values_to_schema(_password_schema(), user_input),
            errors=errors,
            description_placeholders={"name": _title(discovery.name)},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        lights = self._discovered_lights()
        if user_input is not None:
            address = user_input[CONF_ADDRESS].upper()
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            name = lights.get(address, address).split(" (")[0]
            errors = await self._async_verify(address, user_input[CONF_PASSWORD], name)
            if not errors:
                return self.async_create_entry(
                    title=_title(name),
                    data={CONF_ADDRESS: address, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        if not lights:
            return self.async_abort(reason="no_devices_found")
        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): SelectSelector(
                    SelectSelectorConfig(
                        options=[{"value": address, "label": label} for address, label in lights.items()],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_PASSWORD, default=DEFAULT_PASSWORD): _PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """The light stopped accepting the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_verify(entry.data[CONF_ADDRESS], user_input[CONF_PASSWORD], entry.title)
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(_password_schema(), user_input),
            errors=errors,
            description_placeholders={"name": entry.title},
        )


class PawflyOptionsFlow(OptionsFlowWithReload):
    """Link policy: hold the link or connect on demand, poll interval, preferred proxy.

    ``OptionsFlowWithReload`` reloads the entry after a change, which is what makes a new
    link policy (and a new preferred proxy) take effect.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_KEEP_CONNECTED: user_input[CONF_KEEP_CONNECTED],
                    CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL]),
                    CONF_PREFERRED_PROXY: _stored_proxy(user_input.get(CONF_PREFERRED_PROXY)),
                },
            )
        options = self.config_entry.options
        current_proxy = options.get(CONF_PREFERRED_PROXY, "")
        # ESPHome node names of the proxies Home Assistant can connect through right now; a
        # proxy that was picked earlier stays listed even while it is offline, so saving the
        # form again cannot silently drop it.
        proxies = {
            scanner.adapter
            for scanner in bluetooth.async_current_scanners(self.hass)
            if scanner.connectable and getattr(scanner, "adapter", None)
        }
        if current_proxy:
            proxies.add(current_proxy)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_KEEP_CONNECTED, default=options.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED)
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_POLL_INTERVAL, default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_POLL_INTERVAL,
                            max=MAX_POLL_INTERVAL,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                    vol.Optional(CONF_PREFERRED_PROXY, default=current_proxy or PROXY_AUTOMATIC): SelectSelector(
                        SelectSelectorConfig(
                            options=[PROXY_AUTOMATIC, *sorted(proxies - {PROXY_AUTOMATIC})],
                            mode=SelectSelectorMode.DROPDOWN,
                            custom_value=True,
                            translation_key=CONF_PREFERRED_PROXY,
                        )
                    ),
                }
            ),
        )
