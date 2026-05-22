"""EP Cube number entities.

Exposes the two always-on reserve SoCs (self-consumption mode and backup
mode) as adjustable percentages. The cube carries both reserves
simultaneously regardless of current workStatus, so we expose both as
independent number entities — matching the EP Cube app's UX.

Shim interaction: writes patch the shim's captured baseline so the
auto-revert at end_time honours the user's new ground truth. The active
override window itself is not disturbed.

Ranges: cube documentation does not publish hard limits. Empirically the
EP Cube app accepts 5-100 for self-consumption and 10-100 for backup;
defaults observed on this account are 20 and 100 respectively. Widening
the lower bound to 5 / 10 keeps the slider permissive without offering
clearly-rejected values.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DeviceStatus, EPCubeClient, EPCubeError, payload_from_switch_mode_read
from .const import DOMAIN
from .coordinator import EPCubeCoordinator
from .services import PredbatShim

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EPCubeReserveDescription(NumberEntityDescription):
    value_fn: Callable[[DeviceStatus], float]
    wire_field: str


RESERVES: tuple[EPCubeReserveDescription, ...] = (
    EPCubeReserveDescription(
        key="self_consumption_reserve",
        translation_key="self_consumption_reserve",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=5,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: s.self_consumption_reserve_pct,
        wire_field="selfConsumptioinReserveSoc",
    ),
    EPCubeReserveDescription(
        key="backup_reserve",
        translation_key="backup_reserve",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=10,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: s.backup_reserve_pct,
        wire_field="backupPowerReserveSoc",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EPCubeReserveNumber(
            coordinator=data["coordinator"],
            client=data["client"],
            shim=data["shim"],
            entry_id=entry.entry_id,
            description=desc,
        )
        for desc in RESERVES
    )


class EPCubeReserveNumber(CoordinatorEntity[EPCubeCoordinator], NumberEntity):
    _attr_has_entity_name = True
    entity_description: EPCubeReserveDescription

    def __init__(
        self,
        *,
        coordinator: EPCubeCoordinator,
        client: EPCubeClient,
        shim: PredbatShim,
        entry_id: str,
        description: EPCubeReserveDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._client = client
        self._shim = shim
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="EP Cube",
            manufacturer="Canadian Solar",
            model="EP Cube",
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        wire_value = str(int(value))
        field = self.entity_description.wire_field
        try:
            live = await self._client.get_switch_mode()
            payload = payload_from_switch_mode_read(
                self._client.dev_id,
                live,
                overrides={field: wire_value},
            )
            await self._client.switch_mode(payload, verify_keys=(field,))
        except EPCubeError as err:
            raise HomeAssistantError(
                f"failed to set {self.entity_description.key}={wire_value}: {err}"
            ) from err

        self._shim.patch_baseline(field, wire_value)
        await self.coordinator.async_request_refresh()
