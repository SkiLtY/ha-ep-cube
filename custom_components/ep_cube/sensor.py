"""EP Cube sensor entities."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
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
        # Cube reports SoC as integer percent, so kWh resolution is
        # ~capacity/100 (≈0.2 kWh on a 20 kWh stack). 1dp is plenty;
        # default 2dp implies misleading 10 Wh precision.
        suggested_display_precision=1,
        value_fn=lambda s: s.soc_kwh,
    ),
    EPCubeSensorDescription(
        key="battery_capacity_kwh",
        translation_key="battery_capacity_kwh",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # Always batteryPackNum × 5.0 (integer pack count, fixed pack
        # capacity). 1dp matches battery_soc_kwh for dashboard consistency.
        suggested_display_precision=1,
        # Static device spec, not a live metric — keep it off the primary
        # dashboard surface.
        entity_category=EntityCategory.DIAGNOSTIC,
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
        # Duplicated by select.ep_cube_operating_mode since session 16; kept
        # as DIAGNOSTIC for read-only templates / automations referencing the
        # mode string without going through the select.
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.operating_mode,
    ),
    EPCubeSensorDescription(
        key="reserve_soc",
        translation_key="reserve_soc",
        native_unit_of_measurement=PERCENTAGE,
        # Duplicated by number.ep_cube_{self_consumption,backup}_reserve since
        # session 16; this sensor reports whichever reserve the current
        # operating mode uses, so it's still useful as a read-only summary.
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.reserve_soc_pct,
    ),
    # ------------------------------------------------------------------
    # Daily kWh counters from homeDeviceInfo (Phase 3.4 (i)).
    # Cube's onboard accounting — matches the EP Cube mobile app's daily
    # totals. TOTAL_INCREASING handles the midnight reset. Layered into
    # monthly + yearly rollups via utility_meter in ha_config/packages/.
    # ------------------------------------------------------------------
    EPCubeSensorDescription(
        key="solar_today",
        translation_key="solar_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: s.solar_today_kwh,
    ),
    EPCubeSensorDescription(
        key="grid_today",
        translation_key="grid_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: s.grid_today_kwh,
    ),
    EPCubeSensorDescription(
        key="backup_today",
        translation_key="backup_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: s.backup_today_kwh,
    ),
    EPCubeSensorDescription(
        key="nonbackup_today",
        translation_key="nonbackup_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: s.nonbackup_today_kwh,
    ),
    EPCubeSensorDescription(
        key="self_consumption_pct",
        translation_key="self_consumption_pct",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        value_fn=lambda s: s.self_consumption_pct,
    ),
    # Pre-/post-inverter daily kWh — exposes inverter losses over time.
    # DIAGNOSTIC because solar_today already covers the headline number;
    # most users don't need DC/AC split.
    EPCubeSensorDescription(
        key="solar_dc_today",
        translation_key="solar_dc_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.solar_dc_today_kwh,
    ),
    EPCubeSensorDescription(
        key="solar_ac_today",
        translation_key="solar_ac_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.solar_ac_today_kwh,
    ),
    # Read-only DIAGNOSTIC for now — winterProtect appears in homeDeviceInfo
    # but is NOT in getSwitchMode payload, so the write path is unconfirmed.
    # Revisit promoting to a writable number entity after a future capture
    # session reveals the write endpoint.
    EPCubeSensorDescription(
        key="winter_protect",
        translation_key="winter_protect",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.winter_protect_pct,
    ),
    # ------------------------------------------------------------------
    # Phase 3.5 — Bobsilvio-parity metric additions. All read-only, all
    # piggybacking the existing homeDeviceInfo poll except battery_*_today
    # which are computed client-side (cube doesn't expose signed flow).
    # ------------------------------------------------------------------
    # Yesterday's revenue per cube's own accounting. Currency-typed (£ on EU
    # firmware via unitDefault). Most users on Predbat will get richer data
    # via BottlecapDave's Octopus integration anyway — this is for users
    # who configured tariff prices directly in the EP Cube app.
    EPCubeSensorDescription(
        key="earning_yesterday",
        translation_key="earning_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        # GBP hard-coded for now — non-EU users (US/JP/Other) will need a
        # locale-aware unit. Deferred; cube returns the unit string in
        # `unitDefault` on the same payload, but currency-class sensors
        # don't tolerate runtime unit changes well in HA. Revisit when a
        # non-GBP user files an issue.
        native_unit_of_measurement="GBP",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda s: s.earning_yesterday,
    ),
    # Lifetime count of grid power failures the cube has observed. Useful
    # for "how often is my grid actually going down?" automations. Diagnostic
    # because it's a lifetime monotonic counter, not a live metric.
    EPCubeSensorDescription(
        key="grid_outage_count",
        translation_key="grid_outage_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.grid_outage_count,
    ),
    # Lifetime seconds the cube has spent supplying backup loads off-grid.
    # Duration-typed so HA can format as "X days Y hours" in the UI.
    EPCubeSensorDescription(
        key="off_grid_seconds",
        translation_key="off_grid_seconds",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.off_grid_seconds,
    ),
    # Client-side delta-tracker on batteryCurrentElectricity. Cube doesn't
    # expose signed battery flow — these are computed in EPCubeClient via
    # successive-poll deltas above a 0.05 kWh jitter threshold. Resets at
    # local midnight. Pair with utility_meter in ha_config/packages/ for
    # monthly/yearly rollups (see Phase 3.5 commit). Note: lifetime totals
    # are intentionally not surfaced — they would only persist across HA
    # restarts via RestoreSensor + state seeding, which we'd implement in
    # Phase 4.2 alongside the queryDataElectricityV2 capture work.
    EPCubeSensorDescription(
        key="battery_charge_today",
        translation_key="battery_charge_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: s.battery_charge_today_kwh,
    ),
    EPCubeSensorDescription(
        key="battery_discharge_today",
        translation_key="battery_discharge_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda s: s.battery_discharge_today_kwh,
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


class EPCubeSensor(CoordinatorEntity[EPCubeCoordinator], RestoreSensor):
    """EP Cube sensor with persisted last value across HA restarts.

    RestoreSensor: HA's recorder briefly shows the last-known state at
    restart, but our sensor would otherwise override that with `None`
    until the first coordinator poll lands (~30-60s), causing a flicker
    where users see either "Unavailable" or a value that doesn't match
    the cube. Persisting native_value via async_get_last_sensor_data()
    keeps the displayed value stable across restarts until fresh data
    arrives.
    """

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
        self._restored_value: float | str | None = None
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

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._restored_value = last.native_value

    @property
    def native_value(self) -> float | str | None:
        if self.coordinator.data is None:
            return self._restored_value
        return self.entity_description.value_fn(self.coordinator.data)
