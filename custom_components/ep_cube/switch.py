"""EP Cube switch entities.

`switch.ep_cube_allow_grid_charge` mirrors the cube's `allowChargingXiaGrid`
flag (the EP Cube app's "Allow charging from grid" toggle). Reads from the
coordinator; writes go through `client.switch_mode` with the field
overridden via `payload_from_switch_mode_read`.

Shim interaction: unlike the operating-mode select which abandons any
active shim override (mode-switch is a big semantic move), a grid-charge
toggle just patches the captured baseline so the shim's eventual revert
matches the user's new ground truth. No mid-window disruption.
"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EPCubeClient, EPCubeError, payload_from_switch_mode_read
from .const import DOMAIN
from .coordinator import EPCubeCoordinator
from .services import PredbatShim

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [EPCubeAllowGridChargeSwitch(
            coordinator=data["coordinator"],
            client=data["client"],
            shim=data["shim"],
            entry_id=entry.entry_id,
        )]
    )


class EPCubeAllowGridChargeSwitch(CoordinatorEntity[EPCubeCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "allow_grid_charge"

    def __init__(
        self,
        *,
        coordinator: EPCubeCoordinator,
        client: EPCubeClient,
        shim: PredbatShim,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._shim = shim
        self._attr_unique_id = f"{entry_id}_allow_grid_charge"
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
        return self.coordinator.data.allow_grid_charge

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(False)

    async def _write(self, allow: bool) -> None:
        wire_value = "1" if allow else "0"
        try:
            live = await self._client.get_switch_mode()
            payload = payload_from_switch_mode_read(
                self._client.dev_id,
                live,
                overrides={"allowChargingXiaGrid": wire_value},
            )
            await self._client.switch_mode(
                payload, verify_keys=("allowChargingXiaGrid",),
            )
        except EPCubeError as err:
            raise HomeAssistantError(f"failed to set allow_grid_charge={allow}: {err}") from err

        self._shim.patch_baseline("allowChargingXiaGrid", wire_value)
        await self.coordinator.async_request_refresh()
