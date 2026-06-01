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
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DeviceStatus, EPCubeClient, EPCubeError, payload_from_switch_mode_read
from .const import (
    DOMAIN,
    OPERATING_MODE_BACKUP,
    OPERATING_MODE_SELF_CONSUMPTION,
    OPERATING_MODE_TOU,
)
from .coordinator import EPCubeCoordinator
from .services import PredbatShim

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EPCubeReserveDescription(NumberEntityDescription):
    value_fn: Callable[[DeviceStatus], float]
    wire_field: str
    # Operating modes in which the cube actually accepts writes for this
    # reserve. Anything outside this set is rejected (silently — cube
    # returns 200 OK but readback shows the old value, see TROUBLESHOOTING).
    allowed_modes: tuple[str, ...]


RESERVES: tuple[EPCubeReserveDescription, ...] = (
    EPCubeReserveDescription(
        key="self_consumption_reserve",
        translation_key="self_consumption_reserve",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: s.self_consumption_reserve_pct,
        wire_field="selfConsumptioinReserveSoc",
        # TOU shares the self-consumption reserve as its "outside-window"
        # fallback (see api.py::_reserve_for_mode). Permissive availability
        # in TOU mode is provisional — if the cube rejects the write the
        # pre-check + WriteVerificationError will surface that empirically.
        allowed_modes=(OPERATING_MODE_SELF_CONSUMPTION, OPERATING_MODE_TOU),
    ),
    EPCubeReserveDescription(
        key="backup_reserve",
        translation_key="backup_reserve",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=50,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=lambda s: s.backup_reserve_pct,
        wire_field="backupPowerReserveSoc",
        allowed_modes=(OPERATING_MODE_BACKUP,),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = [
        EPCubeReserveNumber(
            coordinator=data["coordinator"],
            client=data["client"],
            shim=data["shim"],
            entry_id=entry.entry_id,
            description=desc,
        )
        for desc in RESERVES
    ]
    entities.append(EPCubePredbatChargeLimit(entry_id=entry.entry_id))
    async_add_entities(entities)


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
            in self.entity_description.allowed_modes
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        wire_value = str(int(value))
        field = self.entity_description.wire_field
        allowed_modes = self.entity_description.allowed_modes
        current_mode = (
            self.coordinator.data.operating_mode if self.coordinator.data else None
        )
        if current_mode not in allowed_modes:
            raise HomeAssistantError(
                f"cannot adjust {self.entity_description.key} while operating "
                f"mode is {current_mode!r} — cube silently ignores reserve "
                f"writes outside the matching mode. Switch the operating-mode "
                f"select to one of {allowed_modes!r} first."
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


class EPCubePredbatChargeLimit(NumberEntity, RestoreEntity):
    """Predbat charge-limit target (SoC %) stub entity.

    Predbat's `charge_limit` apps.yaml key requires an entity that accepts
    `number/set_value` writes and reads back the written value (Predbat polls
    until match). This entity satisfies that polling contract; the actual
    cube actions are still driven by the 6 `*_service` calls Predbat fires
    alongside (`charge_start_service` et al.) which our services.py already
    handles via the shim.

    Default 100% mirrors Predbat's own `create_entity("charge_limit", 100)`
    fallback (see inverter.py:517 in the predbat container).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "predbat_charge_limit"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, *, entry_id: str) -> None:
        self._attr_unique_id = f"{entry_id}_predbat_charge_limit"
        self._attr_native_value = 100.0
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="EP Cube",
            manufacturer="Canadian Solar",
            model="EP Cube",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or last.state in (None, "unknown", "unavailable"):
            return
        try:
            self._attr_native_value = float(last.state)
        except (TypeError, ValueError):
            _LOGGER.debug(
                "predbat_charge_limit: ignoring un-parseable restored state %r",
                last.state,
            )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
