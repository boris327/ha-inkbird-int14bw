"""The Inkbird INT-14-BW integration."""
from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ADDRESS,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_TRANSPORT,
    DEFAULT_TRANSPORT,
    TRANSPORT_WIFI,
)
from .coordinator import InkbirdCoordinator
from .tuya_lan import lan_config_from_options

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type InkbirdConfigEntry = ConfigEntry[InkbirdCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: InkbirdConfigEntry) -> bool:
    """Set up Inkbird INT-14-BW from a config entry."""
    address: str = entry.data[CONF_ADDRESS].upper()
    transport = entry.options.get(CONF_TRANSPORT, DEFAULT_TRANSPORT)
    lan_config = lan_config_from_options(entry.options)

    if transport != TRANSPORT_WIFI:
        # Ensure a Bluetooth adapter/proxy capable of connecting is present.
        if not bluetooth.async_scanner_count(hass, connectable=True):
            raise ConfigEntryNotReady(
                "No connectable Bluetooth adapter or proxy is available"
            )
    elif lan_config is None or not lan_config.is_complete:
        raise ConfigEntryNotReady(
            "Wi-Fi only mode is selected but the Wi-Fi (Tuya LAN) settings "
            "are incomplete - open Configure and fill in host, device ID and "
            "local key, or switch the connection mode back"
        )

    coordinator = InkbirdCoordinator(
        hass, address, transport=transport, lan_config=lan_config
    )
    await coordinator.async_start()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Stop the connection loop promptly on HA shutdown so it doesn't delay it.
    async def _on_stop(_event: Event) -> None:
        await coordinator.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)
    )
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: InkbirdConfigEntry
) -> None:
    """Reload the entry when options (e.g. temperature unit) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: InkbirdConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok
