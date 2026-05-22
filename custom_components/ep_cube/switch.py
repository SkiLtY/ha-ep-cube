"""EP Cube switch entities.

Two switches both restricted to TOU mode (the EP Cube app only surfaces
them there, and empirically the cube cloud silently ignores writes for
these fields outside TOU — same constraint pattern as the reserve numbers,
see TROUBLESHOOTING.md):

- `switch.ep_cube_allow_grid_charge` mirrors `allowChargingXiaGrid` —
  wire-typed as string ("1"/"0").
- `switch.ep_cube_daylight_saving_time` mirrors `dayLightSavingTime` —
  wire-typed as native bool.

Shim interaction: writes patch the shim's captured baseline so an active
override's auto-revert honours the user's new ground truth (no mid-window
disruption — same model as the reserve numbers).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DeviceStatus, EPCubeClient, EPCubeError, payload_from_switch_mode_read
from .const import DOMAIN, OPERATING_MODE_TOU
from .coordinator import EPCubeCoordinator
from .services import PredbatShim

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EPCubeSwitchDescription(SwitchEntityDescription):
    value_fn: Callable[[DeviceStatus], bool]
    wire_field: str
    wire_on: Any   # "1" for string-typed fields, True for bool-typed fields
    wire_off: Any  # "0" / False respectively
    allowed_modes: tuple[str, ...]


SWITCHES: tuple[EPCubeSwitchDescription, ...] = (
    EPCubeSwitchDescription(
        key="allow_grid_charge",
        translation_key="allow_grid_charge",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.allow_grid_charge,
        wire_field="allowChargingXiaGrid",
        wire_on="1",
        wire_off="0",
        allowed_modes=(OPERATING_MODE_TOU,),
    ),
    EPCubeSwitchDescription(
        key="daylight_saving_time",
        translation_key="daylight_saving_time",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.dst_active,
        wire_field="dayLightSavingTime",
        wire_on=True,
        wire_off=False,
        allowed_modes=(OPERATING_MODE_TOU,),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EPCubeSwitch(
            coordinator=data["coordinator"],
            client=data["client"],
            shim=data["shim"],
            entry_id=entry.entry_id,
            description=desc,
        )
        for desc in SWITCHES
    )


class EPCubeSwitch(CoordinatorEntity[EPCubeCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    entity_description: EPCubeSwitchDescription

    def __init__(
        self,
        *,
        coordinator: EPCubeCoordinator,
        client: EPCubeClient,
        shim: PredbatShim,
        entry_id: str,
        description: EPCubeSwitchDescription,
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
        """Only available in modes that actually accept the write — see
        module docstring for the constraint origin."""
        if not super().available or self.coordinator.data is None:
            return False
        return (
            self.coordinator.data.operating_mode
            in self.entity_description.allowed_modes
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(self.entity_description.wire_on)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(self.entity_description.wire_off)

    async def _write(self, wire_value: Any) -> None:
        field = self.entity_description.wire_field
        allowed_modes = self.entity_description.allowed_modes
        current_mode = (
            self.coordinator.data.operating_mode if self.coordinator.data else None
        )
        if current_mode not in allowed_modes:
            raise HomeAssistantError(
                f"cannot toggle {self.entity_description.key} while operating "
                f"mode is {current_mode!r} — cube silently ignores writes for "
                f"this field outside {allowed_modes!r}. Switch the operating-mode "
                f"select first."
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
