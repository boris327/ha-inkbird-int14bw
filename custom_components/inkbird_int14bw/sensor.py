"""Sensor platform for the Inkbird INT-14-BW."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_ADDRESS,
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import InkbirdConfigEntry
from .const import (
    CONF_TEMP_UNIT,
    DEFAULT_TEMP_UNIT,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    NUM_PROBES,
    UNIT_FAHRENHEIT,
)
from .coordinator import InkbirdCoordinator


@dataclass(frozen=True, kw_only=True)
class InkbirdSensorDescription(SensorEntityDescription):
    """Describes an Inkbird sensor and how to read it from coordinator data."""

    value_fn: Callable[[InkbirdCoordinator], float | int | None]


def _probe_description(index: int) -> InkbirdSensorDescription:
    return InkbirdSensorDescription(
        key=f"probe{index + 1}",
        translation_key=f"probe{index + 1}",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda c, i=index: c.data.probes[i],
    )


def _ambient_description(index: int) -> InkbirdSensorDescription:
    # Each probe also reports the ambient (grill/oven air) temperature around
    # its tip. Off by default to keep the entity list tidy; enable per probe
    # when monitoring pit/oven temperature.
    return InkbirdSensorDescription(
        key=f"probe{index + 1}_ambient",
        translation_key=f"probe{index + 1}_ambient",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda c, i=index: c.data.ambient[i],
    )


SENSOR_DESCRIPTIONS: tuple[InkbirdSensorDescription, ...] = (
    *(_probe_description(i) for i in range(NUM_PROBES)),
    *(_ambient_description(i) for i in range(NUM_PROBES)),
    InkbirdSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.battery,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InkbirdConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Inkbird sensors from a config entry."""
    coordinator = entry.runtime_data
    address = entry.data[CONF_ADDRESS].upper()
    unit = entry.options.get(CONF_TEMP_UNIT, DEFAULT_TEMP_UNIT)
    async_add_entities(
        InkbirdSensor(coordinator, address, description, unit)
        for description in SENSOR_DESCRIPTIONS
    )


class InkbirdSensor(SensorEntity):
    """Representation of an Inkbird INT-14-BW sensor."""

    _attr_has_entity_name = True
    entity_description: InkbirdSensorDescription

    def __init__(
        self,
        coordinator: InkbirdCoordinator,
        address: str,
        description: InkbirdSensorDescription,
        unit: str,
    ) -> None:
        self.coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{address}_{description.key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, address)},
            identifiers={(DOMAIN, address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=MODEL,
        )
        # For temperature probes, honour an explicit Fahrenheit choice.
        # "auto"/"celsius" keep the device's native °C — with device_class
        # temperature, Home Assistant still converts °C to the user's unit
        # system for display, and per-entity overrides remain available.
        self._to_fahrenheit = (
            unit == UNIT_FAHRENHEIT
            and description.device_class == SensorDeviceClass.TEMPERATURE
        )
        if self._to_fahrenheit:
            self._attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT

    @property
    def native_value(self) -> float | int | None:
        value = self.entity_description.value_fn(self.coordinator)
        if value is None or not self._to_fahrenheit:
            return value
        return round(value * 9 / 5 + 32, 1)

    @property
    def available(self) -> bool:
        return self.coordinator.available

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        # Only the diagnostic battery sensor carries this - no need to
        # repeat the same attribute on every probe/ambient entity too.
        if self.entity_description.key != "battery":
            return None
        transport = self.coordinator.active_transport
        if transport is None:
            return None
        return {"active_transport": transport}

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()