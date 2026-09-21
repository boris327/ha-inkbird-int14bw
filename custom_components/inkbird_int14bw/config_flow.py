"""Config flow for the Inkbird INT-14-BW integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_TEMP_UNIT,
    DEFAULT_TEMP_UNIT,
    DOMAIN,
    LOCAL_NAME,
    is_supported_name,
    MODEL,
    UNIT_AUTO,
    UNIT_CELSIUS,
    UNIT_FAHRENHEIT,
)


def _is_supported(info: BluetoothServiceInfoBleak) -> bool:
    """Return True if an advertisement looks like an INT-14-BW."""
    return is_supported_name(info.name)


class InkbirdConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Inkbird INT-14-BW."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address: str | None = None
        self._discovered_devices: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow (temperature unit)."""
        return InkbirdOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered automatically via Bluetooth."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        if not _is_supported(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovered_address = discovery_info.address
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding an auto-discovered device."""
        assert self._discovered_address is not None
        if user_input is not None:
            return self.async_create_entry(
                title=f"{MODEL} ({self._discovered_address})",
                data={CONF_ADDRESS: self._discovered_address},
            )
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"address": self._discovered_address},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow started by the user from the UI.

        The only thing the user must supply is the device's Bluetooth MAC
        address, found in the Inkbird app under Settings -> Device
        Information. If the HA Bluetooth stack has already seen the device, we
        also list it so it can be picked from a dropdown instead of typed.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip()
            formatted = format_mac(address)
            if len(formatted) != 17 or formatted.count(":") != 5:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(
                    formatted, raise_on_progress=False
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{MODEL} ({formatted.upper()})",
                    data={CONF_ADDRESS: formatted.upper()},
                )

        current = self._async_current_ids()
        for info in async_discovered_service_info(self.hass):
            if format_mac(info.address) in current:
                continue
            if _is_supported(info):
                self._discovered_devices[info.address] = (
                    f"{info.name} ({info.address})"
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
            errors=errors,
            description_placeholders={
                "discovered": ", ".join(self._discovered_devices.values())
                or "none seen yet"
            },
        )


class InkbirdOptionsFlow(OptionsFlow):
    """Options: choose the temperature unit for the probe sensors."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(CONF_TEMP_UNIT, DEFAULT_TEMP_UNIT)
        unit_selector = SelectSelector(
            SelectSelectorConfig(
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="temperature_unit",
                options=[
                    SelectOptionDict(value=UNIT_AUTO, label="Follow Home Assistant"),
                    SelectOptionDict(value=UNIT_CELSIUS, label="Celsius (°C)"),
                    SelectOptionDict(value=UNIT_FAHRENHEIT, label="Fahrenheit (°F)"),
                ],
            )
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_TEMP_UNIT, default=current): unit_selector}
            ),
        )
