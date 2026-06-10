"""Tests for v1.2 derived percentage value_fns.

`_self_consumption_pct` and `_self_sufficiency_pct` are pure functions over
the stats coordinator's bucket dict — testable without HA bootstrap. They
must:
  - return None when the bucket is empty / missing / non-numeric
  - return None when the divisor is below the cube's jitter threshold
  - clamp output to [0, 100] so transient noise doesn't surface negative pct
  - compute the ratio against the right fields (solar/export for SC, load/import
    for SS)

The instant-KPI helpers (`_instant_self_consumption_pct`,
`_instant_self_sufficiency_pct`, `_instant_grid_flow_w`) operate over
DeviceStatus power readings; same shape, different units (W not kWh) and
different thresholds (50 W noise floor / 200 W dead-band).
"""
from __future__ import annotations

import pytest

from custom_components.ep_cube.api import DeviceStatus
from custom_components.ep_cube.sensor import (
    _INSTANT_GRID_FLOW_DEADBAND_W,
    _INSTANT_MIN_POWER_W,
    _MIN_DENOMINATOR_KWH,
    _instant_grid_flow_w,
    _instant_self_consumption_pct,
    _instant_self_sufficiency_pct,
    _self_consumption_pct,
    _self_sufficiency_pct,
)


def _ds(*, solar_w: float = 0.0, grid_w: float = 0.0, load_w: float = 0.0) -> DeviceStatus:
    """Minimal DeviceStatus stub for instant-KPI value_fn tests.

    Only the three power-W fields exercised by the instant helpers are set
    to non-defaults — everything else gets a benign zero / empty value.
    """
    return DeviceStatus(
        soc_pct=50.0,
        soc_kwh=10.0,
        capacity_kwh=20.0,
        battery_power_w=0.0,
        grid_power_w=grid_w,
        solar_power_w=solar_w,
        load_power_w=load_w,
        operating_mode="self_consumption",
        reserve_soc_pct=20.0,
        allow_grid_charge=True,
        self_consumption_reserve_pct=20.0,
        backup_reserve_pct=100.0,
        dst_active=False,
        solar_today_kwh=0.0,
        backup_today_kwh=0.0,
        solar_dc_today_kwh=0.0,
        solar_ac_today_kwh=0.0,
        self_sufficiency_pct=0.0,
        winter_protect_pct=0.0,
        earning_yesterday=0.0,
        grid_outage_count=0,
        off_grid_seconds=0,
        battery_charge_today_kwh=0.0,
        battery_discharge_today_kwh=0.0,
    )


# ----------------------------------------------------------------------
# _self_consumption_pct — (solar - export) / solar
# ----------------------------------------------------------------------
class TestSelfConsumptionPct:
    def test_normal_case(self):
        # 30 kWh solar, 12 kWh export → (30-12)/30 = 60%
        data = {"today": {"solarelectricity": 30.0, "gridelectricityto": 12.0}}
        assert _self_consumption_pct("today")(data) == pytest.approx(60.0)

    def test_all_self_consumed(self):
        # Export = 0 → 100% self-consumption
        data = {"today": {"solarelectricity": 10.0, "gridelectricityto": 0.0}}
        assert _self_consumption_pct("today")(data) == pytest.approx(100.0)

    def test_solar_below_threshold_returns_none(self):
        # Below jitter floor: ratio is meaningless, return unknown.
        data = {"today": {"solarelectricity": 0.04, "gridelectricityto": 0.0}}
        assert _self_consumption_pct("today")(data) is None

    def test_solar_at_threshold_computes(self):
        # Floor is inclusive of valid range — exactly at threshold compute.
        data = {
            "today": {
                "solarelectricity": _MIN_DENOMINATOR_KWH,
                "gridelectricityto": 0.0,
            }
        }
        assert _self_consumption_pct("today")(data) == pytest.approx(100.0)

    def test_export_exceeds_solar_clamps_to_zero(self):
        # Rare boot-up artefact: clamp to 0 rather than surface negative pct.
        data = {"today": {"solarelectricity": 5.0, "gridelectricityto": 10.0}}
        assert _self_consumption_pct("today")(data) == 0.0

    def test_empty_data_returns_none(self):
        assert _self_consumption_pct("today")({}) is None
        assert _self_consumption_pct("today")(None) is None  # type: ignore[arg-type]

    def test_missing_bucket_returns_none(self):
        # Bucket not yet populated — sensor reads `unknown` not crashes.
        data = {"yesterday": {"solarelectricity": 30.0, "gridelectricityto": 5.0}}
        assert _self_consumption_pct("today")(data) is None

    def test_missing_field_returns_none(self):
        # Coordinator returned a partial response (network blip mid-payload).
        data = {"today": {"solarelectricity": 30.0}}  # no export field
        assert _self_consumption_pct("today")(data) is None

    def test_non_numeric_returns_none(self):
        data = {"today": {"solarelectricity": "n/a", "gridelectricityto": 0.0}}
        assert _self_consumption_pct("today")(data) is None

    def test_yesterday_bucket_routing(self):
        # Confirms the bucket arg actually targets `yesterday`.
        data = {
            "today": {"solarelectricity": 30.0, "gridelectricityto": 5.0},
            "yesterday": {"solarelectricity": 20.0, "gridelectricityto": 10.0},
        }
        assert _self_consumption_pct("yesterday")(data) == pytest.approx(50.0)


# ----------------------------------------------------------------------
# _self_sufficiency_pct — (load - import) / load
# ----------------------------------------------------------------------
class TestSelfSufficiencyPct:
    def test_normal_case(self):
        # 15 kWh load, 0.25 kWh import → (15-0.25)/15 = 98.33%
        data = {"today": {"backupelectricity": 15.0, "gridelectricityfrom": 0.25}}
        assert _self_sufficiency_pct("today")(data) == pytest.approx(98.333, rel=1e-3)

    def test_full_self_sufficient(self):
        # Import = 0 → 100%
        data = {"today": {"backupelectricity": 10.0, "gridelectricityfrom": 0.0}}
        assert _self_sufficiency_pct("today")(data) == pytest.approx(100.0)

    def test_full_grid_dependent(self):
        # Import == load → 0%
        data = {"today": {"backupelectricity": 10.0, "gridelectricityfrom": 10.0}}
        assert _self_sufficiency_pct("today")(data) == pytest.approx(0.0)

    def test_load_below_threshold_returns_none(self):
        data = {"today": {"backupelectricity": 0.04, "gridelectricityfrom": 0.0}}
        assert _self_sufficiency_pct("today")(data) is None

    def test_import_exceeds_load_clamps_to_zero(self):
        # Possible if `backupelectricity` is partial-house and import covers
        # the non-backup branch too. Clamp rather than surface negative pct.
        data = {"today": {"backupelectricity": 5.0, "gridelectricityfrom": 8.0}}
        assert _self_sufficiency_pct("today")(data) == 0.0

    def test_empty_data_returns_none(self):
        assert _self_sufficiency_pct("today")({}) is None

    def test_missing_field_returns_none(self):
        data = {"today": {"backupelectricity": 15.0}}
        assert _self_sufficiency_pct("today")(data) is None

    def test_non_numeric_returns_none(self):
        data = {"today": {"backupelectricity": None, "gridelectricityfrom": 0.0}}
        assert _self_sufficiency_pct("today")(data) is None

    def test_yesterday_bucket_routing(self):
        data = {
            "today": {"backupelectricity": 15.0, "gridelectricityfrom": 0.0},
            "yesterday": {"backupelectricity": 20.0, "gridelectricityfrom": 5.0},
        }
        assert _self_sufficiency_pct("yesterday")(data) == pytest.approx(75.0)


# ----------------------------------------------------------------------
# Instant-KPI helpers — derived from DeviceStatus power channels (W not kWh)
# ----------------------------------------------------------------------
class TestInstantSelfConsumptionPct:
    def test_partial_export(self):
        # 4 kW solar, 1 kW export → (4000-1000)/4000 = 75%
        assert _instant_self_consumption_pct(_ds(solar_w=4000, grid_w=-1000)) == pytest.approx(75.0)

    def test_full_self_consumed(self):
        # Solar covers load, nothing exported → 100%
        assert _instant_self_consumption_pct(_ds(solar_w=2000, grid_w=200)) == pytest.approx(100.0)

    def test_solar_below_noise_floor_returns_none(self):
        # Pre-sunrise / overnight: gauge reads `unknown` not a noisy %.
        assert _instant_self_consumption_pct(_ds(solar_w=_INSTANT_MIN_POWER_W - 1)) is None

    def test_solar_at_threshold_computes(self):
        # Inclusive threshold — exactly at floor computes.
        assert _instant_self_consumption_pct(_ds(solar_w=_INSTANT_MIN_POWER_W, grid_w=0)) == pytest.approx(100.0)

    def test_clamp_zero_floor(self):
        # Shouldn't be physically possible (export > solar) but clamp anyway.
        # Use a small export so neg clamp wins.
        result = _instant_self_consumption_pct(_ds(solar_w=100, grid_w=-200))
        assert result == 0.0

    def test_import_ignored_for_self_consumption(self):
        # Import doesn't affect self-consumption — only export does.
        # 3 kW solar + 500 W import → solar > 0, export = 0 → 100% SC.
        assert _instant_self_consumption_pct(_ds(solar_w=3000, grid_w=500)) == pytest.approx(100.0)


class TestInstantSelfSufficiencyPct:
    def test_partial_import(self):
        # 4 kW load, 1 kW import → (4000-1000)/4000 = 75%
        assert _instant_self_sufficiency_pct(_ds(load_w=4000, grid_w=1000)) == pytest.approx(75.0)

    def test_full_self_sufficient(self):
        # Load met from solar/battery, exporting → 100%.
        assert _instant_self_sufficiency_pct(_ds(load_w=3000, grid_w=-500)) == pytest.approx(100.0)

    def test_load_below_noise_floor_returns_none(self):
        # Empty house: gauge reads `unknown`.
        assert _instant_self_sufficiency_pct(_ds(load_w=_INSTANT_MIN_POWER_W - 1)) is None

    def test_load_at_threshold_computes(self):
        assert _instant_self_sufficiency_pct(_ds(load_w=_INSTANT_MIN_POWER_W, grid_w=0)) == pytest.approx(100.0)

    def test_full_grid_dependent(self):
        # Import == load → 0%
        assert _instant_self_sufficiency_pct(_ds(load_w=2000, grid_w=2000)) == pytest.approx(0.0)

    def test_clamp_zero_floor(self):
        # Import > load (e.g. battery charging from grid) — clamp.
        result = _instant_self_sufficiency_pct(_ds(load_w=500, grid_w=2000))
        assert result == 0.0

    def test_export_ignored_for_self_sufficiency(self):
        # Export doesn't affect sufficiency — only import does.
        assert _instant_self_sufficiency_pct(_ds(load_w=2000, grid_w=-1500)) == pytest.approx(100.0)


class TestInstantGridFlowW:
    def test_passes_through_import(self):
        # Above dead-band: signed value passes through.
        assert _instant_grid_flow_w(_ds(grid_w=1500)) == 1500.0

    def test_passes_through_export(self):
        assert _instant_grid_flow_w(_ds(grid_w=-2300)) == -2300.0

    def test_zero_inside_deadband_positive(self):
        # Just-importing within dead-band: round to 0.
        assert _instant_grid_flow_w(_ds(grid_w=_INSTANT_GRID_FLOW_DEADBAND_W - 1)) == 0.0

    def test_zero_inside_deadband_negative(self):
        # Just-exporting within dead-band: round to 0.
        assert _instant_grid_flow_w(_ds(grid_w=-(_INSTANT_GRID_FLOW_DEADBAND_W - 1))) == 0.0

    def test_at_deadband_edge_passes_through(self):
        # Exactly at the dead-band: edge passes through (strict <).
        assert _instant_grid_flow_w(_ds(grid_w=_INSTANT_GRID_FLOW_DEADBAND_W)) == _INSTANT_GRID_FLOW_DEADBAND_W

    def test_exact_zero(self):
        assert _instant_grid_flow_w(_ds(grid_w=0.0)) == 0.0
