"""Tests for v1.2 derived percentage value_fns.

`_self_consumption_pct` and `_self_sufficiency_pct` are pure functions over
the stats coordinator's bucket dict — testable without HA bootstrap. They
must:
  - return None when the bucket is empty / missing / non-numeric
  - return None when the divisor is below the cube's jitter threshold
  - clamp output to [0, 100] so transient noise doesn't surface negative pct
  - compute the ratio against the right fields (solar/export for SC, load/import
    for SS)
"""
from __future__ import annotations

import pytest

from custom_components.ep_cube.sensor import (
    _MIN_DENOMINATOR_KWH,
    _self_consumption_pct,
    _self_sufficiency_pct,
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
