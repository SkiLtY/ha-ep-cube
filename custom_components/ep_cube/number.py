"""EP Cube number entities.

Exposes the two always-on reserve SoCs (self-consumption mode and backup
mode) as adjustable percentages. The cube carries both reserves
simultaneously regardless of current workStatus, so we expose both as
independent number entities — matching the EP Cube app's UX.

Shim interaction: writes patch the shim's captured baseline so the
auto-revert at end_time honours the user's new ground truth. The active
override window itself is not disturbed.

Ranges: cube documentation does not publish hard limits. Empirically the
EP Cube app enforces 5-100 for self-consumption and **50-100 for backup**
(observed 2026-05-22: cube accepts lower wire values but the app refuses
to let users select below 50, presumably because going lower defeats the
purpose of backup-reserve). We match the app's bounds rather than the
cube's permissive wire behaviour to prevent users from stranding the
battery below a usable backup threshold via the HA slider.

Cube quirk (2026-05-22): `switchMode` silently ignores reserve-SoC fields
that don't match the active `workStatus`. Setting `backupPowerReserveSoc`
while in self-consumption mode writes 200 OK but the readback shows the
old value — i.e. the cube discards the change. We pre-check the active
mode before each write and refuse with a clear message if it doesn't
match. The user must switch to the corresponding mode (via the
operating-mode select) before tweaking that reserve.
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
from .const import DOMAIN, OPERATING_MODE_BACKUP, OPERATING_MODE_SELF_CONSUMPTION
from .coordinator import EPCubeCoordinator
from .services import PredbatShim

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EPCubeReserveDescription(NumberEntityDescription):
    value_fn: Callable[[DeviceStatus], float]
    wire_field: str
    required_mode: str  # cube silently ignores writes for non-active-mode reserves


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
        required_mode=OPERATING_MODE_SELF_CONSUMPTION,
    ),
    EPCubeReserveDescription(
        key="backup_reserve",
        translation_key="backup_reserve",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=50,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: s.backup_reserve_pct,
        wire_field="backupPowerReserveSoc",
        required_mode=OPERATING_MODE_BACKUP,
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
    def available(self) -> bool:
        """Slider is only available when the cube is in the matching mode.

        Cube silently ignores reserve writes that don't match the active
        workStatus (see TROUBLESHOOTING.md), so we surface the constraint
        at the UI level — the slider greys out on the device card when
        in the wrong mode. The pre-check in async_set_native_value remains
        as a defensive backstop for direct service calls.
        """
        if not super().available or self.coordinator.data is None:
            return False
        return (
            self.coordinator.data.operating_mode
            == self.entity_description.required_mode
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        wire_value = str(int(value))
        field = self.entity_description.wire_field
        required_mode = self.entity_description.required_mode
        current_mode = (
            self.coordinator.data.operating_mode if self.coordinator.data else None
        )
        if current_mode != required_mode:
            raise HomeAssistantError(
                f"cannot adjust {self.entity_description.key} while operating "
                f"mode is {current_mode!r} — cube silently ignores reserve "
                f"writes outside the matching mode. Switch the operating-mode "
                f"select to {required_mode!r} first."
            )
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
