"""EP Cube operating-mode select entity.

Exposes the cube's `workStatus` (self_consumption / time_of_use / backup) as
a read-write select. Reads come from the coordinator's DeviceStatus; writes
go directly to `client.switch_mode` with a payload built from the current
getSwitchMode snapshot so all other fields (reserves, TOU schedule, day
arrays) are preserved.

Shim interaction: if a Predbat shim override is active when the user picks
a mode, the shim's pending revert is abandoned — manual control wins. See
PredbatShim.abandon_override.
"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EPCubeClient, EPCubeError, payload_from_switch_mode_read
from .const import (
    DOMAIN,
    OPERATING_MODE_BACKUP,
    OPERATING_MODE_SELF_CONSUMPTION,
    OPERATING_MODE_TO_WORK_STATUS,
    OPERATING_MODE_TOU,
)
from .coordinator import EPCubeCoordinator
from .services import PredbatShim, parse_tou_schedule

_LOGGER = logging.getLogger(__name__)

OPERATING_MODE_OPTIONS: list[str] = [
    OPERATING_MODE_SELF_CONSUMPTION,
    OPERATING_MODE_TOU,
    OPERATING_MODE_BACKUP,
]

# Predbat's `inverter_mode` write target. Predbat (inverter.py:2076-2125 in
# the upstream container, generic-inverter branch — not Fox, not GE eco
# toggle) writes one of these two raw strings via `select/select_option`
# and polls until the entity state matches. "Eco" is normal/Demand mode,
# "Timed Export" is force-export-window active. Predbat also tolerates
# reading "Eco (Paused)" as a third state but never writes it, so we omit
# it to keep the option list focused on writable values.
PREDBAT_INVERTER_MODE_OPTIONS: list[str] = ["Eco", "Timed Export"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EPCubeOperatingModeSelect(
                coordinator=data["coordinator"],
                client=data["client"],
                shim=data["shim"],
                entry_id=entry.entry_id,
            ),
            EPCubePredbatInverterMode(entry_id=entry.entry_id),
        ]
    )


class EPCubeOperatingModeSelect(CoordinatorEntity[EPCubeCoordinator], SelectEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "operating_mode"
    _attr_options = OPERATING_MODE_OPTIONS
    _attr_entity_category = EntityCategory.CONFIG

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
        self._attr_unique_id = f"{entry_id}_operating_mode_select"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="EP Cube",
            manufacturer="Canadian Solar",
            model="EP Cube",
        )

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return None
        mode = self.coordinator.data.operating_mode
        return mode if mode in OPERATING_MODE_OPTIONS else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the cube's current TOU schedule as an attribute so the
        editor card can hydrate from real state. Empty until the coordinator
        has successfully fetched getSwitchMode at least once."""
        switch_mode = self.coordinator.switch_mode
        if switch_mode is None:
            return {}
        return {"tou_schedule": parse_tou_schedule(switch_mode)}

    async def async_select_option(self, option: str) -> None:
        if option not in OPERATING_MODE_TO_WORK_STATUS:
            raise HomeAssistantError(f"unknown operating mode: {option!r}")

        if self._shim.is_active:
            _LOGGER.warning(
                "operating_mode select picked %s while shim override active — "
                "abandoning pending revert",
                option,
            )
            self._shim.abandon_override()

        target_work_status = OPERATING_MODE_TO_WORK_STATUS[option]
        try:
            live = await self._client.get_switch_mode()
            payload = payload_from_switch_mode_read(
                self._client.dev_id,
                live,
                overrides={"workStatus": target_work_status},
            )
            await self._client.switch_mode(payload)
        except EPCubeError as err:
            raise HomeAssistantError(f"failed to switch mode to {option}: {err}") from err

        await self.coordinator.async_request_refresh()


class EPCubePredbatInverterMode(SelectEntity, RestoreEntity):
    """Predbat inverter_mode stub entity.

    Predbat's `inverter_mode` apps.yaml key requires a select entity that
    accepts `select/select_option` writes and reads back the written value
    (Predbat polls until match). This entity satisfies that polling contract;
    the actual cube force-export window is still driven by the
    `discharge_start_service` / `discharge_end_service` calls Predbat fires
    alongside, which our services.py already handles via the shim.

    Default "Eco" mirrors Predbat's own `create_entity("inverter_mode",
    "Eco")` fallback (see inverter.py:528-529 in the predbat container).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "predbat_inverter_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = PREDBAT_INVERTER_MODE_OPTIONS

    def __init__(self, *, entry_id: str) -> None:
        self._attr_unique_id = f"{entry_id}_predbat_inverter_mode"
        self._attr_current_option = "Eco"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="EP Cube",
            manufacturer="Canadian Solar",
            model="EP Cube",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in PREDBAT_INVERTER_MODE_OPTIONS:
            self._attr_current_option = last.state

    async def async_select_option(self, option: str) -> None:
        if option not in PREDBAT_INVERTER_MODE_OPTIONS:
            raise HomeAssistantError(
                f"unknown predbat inverter_mode option {option!r} — must be one of "
                f"{PREDBAT_INVERTER_MODE_OPTIONS!r}"
            )
        self._attr_current_option = option
        self.async_write_ha_state()
