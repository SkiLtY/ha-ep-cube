"""EP Cube sensor entities."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DeviceStatus
from .const import DOMAIN
from .coordinator import EPCubeCoordinator


@dataclass(frozen=True, kw_only=True)
class EPCubeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[DeviceStatus], float | str | None]


SENSORS: tuple[EPCubeSensorDescription, ...] = (
    EPCubeSensorDescription(
        key="battery_soc",
        translation_key="battery_soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda s: s.soc_pct,
    ),
    EPCubeSensorDescription(
        key="battery_soc_kwh",
        translation_key="battery_soc_kwh",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda s: s.soc_kwh,
    ),
    EPCubeSensorDescription(
        key="battery_capacity_kwh",
        translation_key="battery_capacity_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda s: s.capacity_kwh,
    ),
    EPCubeSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda s: s.battery_power_w,
    ),
    EPCubeSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda s: s.grid_power_w,
    ),
    EPCubeSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda s: s.solar_power_w,
    ),
    EPCubeSensorDescription(
        key="load_power",
        translation_key="load_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda s: s.load_power_w,
    ),
    EPCubeSensorDescription(
        key="operating_mode",
        translation_key="operating_mode",
        value_fn=lambda s: s.operating_mode,
    ),
    EPCubeSensorDescription(
        key="reserve_soc",
        translation_key="reserve_soc",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda s: s.reserve_soc_pct,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EPCubeCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        EPCubeSensor(coordinator, entry.entry_id, desc) for desc in SENSORS
    )


class EPCubeSensor(CoordinatorEntity[EPCubeCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: EPCubeSensorDescription

    def __init__(
        self,
        coordinator: EPCubeCoordinator,
        entry_id: str,
        description: EPCubeSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        # Device name is intentionally stable across devIds so downstream
        # consumers (Predbat apps.yaml, ha_config/packages/ep_cube.yaml) can
        # reference entity IDs like `sensor.ep_cube_battery_soc` without
        # needing to hard-code a per-account devId slug. Multi-account / dual
        # mock+cloud users disambiguate via the config-entry title
        # (`EP Cube ({dev_id})`) and HA's auto-appended `_2` suffix.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="EP Cube",
            manufacturer="Canadian Solar",
            model="EP Cube",
        )

    @property
    def native_value(self) -> float | str | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
