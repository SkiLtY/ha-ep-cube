"""EP Cube binary sensors: online, fault, alert and the firmware auto-update flag."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DeviceStatus
from .const import DOMAIN
from .coordinator import EPCubeCoordinator


@dataclass(frozen=True, kw_only=True)
class EPCubeBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[DeviceStatus], bool | None]


# Online / Fault / Alert come from the 60 s homeDeviceInfo poll. The cube sends them
# as "1"/"0" strings; a missing key reads `unknown`, never a made-up "off".
BINARY_SENSORS: tuple[EPCubeBinarySensorDescription, ...] = (
    EPCubeBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.is_online,
    ),
    EPCubeBinarySensorDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.is_fault,
    ),
    EPCubeBinarySensorDescription(
        key="alert",
        translation_key="alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.is_alert,
    ),
)

# The firmware auto-update switch comes from the firmware route, not the status poll.
FIRMWARE_AUTO_UPDATE = EPCubeBinarySensorDescription(
    key="firmware_auto_update",
    translation_key="firmware_auto_update",
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda s: None,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EPCubeCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[BinarySensorEntity] = [
        EPCubeBinarySensor(coordinator, entry.entry_id, desc) for desc in BINARY_SENSORS
    ]
    entities.append(EPCubeAutoUpdateBinarySensor(coordinator, entry.entry_id, FIRMWARE_AUTO_UPDATE))
    async_add_entities(entities)


class EPCubeBinarySensor(CoordinatorEntity[EPCubeCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    entity_description: EPCubeBinarySensorDescription

    def __init__(
        self,
        coordinator: EPCubeCoordinator,
        entry_id: str,
        description: EPCubeBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        # Same device as the sensors (see EPCubeSensor).
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="EP Cube",
            manufacturer="Canadian Solar",
            model="EP Cube",
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class EPCubeAutoUpdateBinarySensor(EPCubeBinarySensor):
    """Whether the cube is set to update its own firmware (queryFirmwareInfo)."""

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.firmware is not None

    @property
    def is_on(self) -> bool | None:
        firmware = self.coordinator.firmware
        return firmware.auto_update if firmware is not None else None
