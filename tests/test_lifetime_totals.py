"""Lifetime energy totals (grid import/export, solar, backup loads).

They read the stats coordinator's `total` bucket, carry state class
TOTAL_INCREASING for the Energy dashboard, and never step backwards.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.ep_cube.sensor import STATS_SENSORS, EPCubeStatsSensor

KEYS = {
    "grid_import_total": "gridelectricityfrom",
    "grid_export_total": "gridelectricityto",
    "solar_total": "solarelectricity",
    "backup_total": "backupelectricity",
}
BUCKET = {
    "gridelectricityfrom": 310.5,
    "gridelectricityto": 640.25,
    "solarelectricity": 1500.0,
    "backupelectricity": 980.75,
}


def _desc(key):
    return next(d for d in STATS_SENSORS if d.key == key)


def _sensor(key, data):
    coordinator = SimpleNamespace(data=data)
    return EPCubeStatsSensor(coordinator, "entry", _desc(key))


@pytest.mark.parametrize(("key", "field"), KEYS.items())
def test_reads_the_total_bucket(key, field):
    assert _desc(key).value_fn({"total": BUCKET, "year": {field: 1.0}}) == BUCKET[field]


@pytest.mark.parametrize("key", KEYS)
def test_is_a_total_increasing_energy_counter(key):
    desc = _desc(key)
    assert desc.state_class == SensorStateClass.TOTAL_INCREASING
    assert desc.monotonic is True


@pytest.mark.parametrize("key", KEYS)
def test_unknown_until_fetched(key):
    assert _desc(key).value_fn({}) is None
    assert _desc(key).value_fn({"total": {}}) is None


def test_other_stats_sensors_are_not_monotonic():
    assert not any(d.monotonic for d in STATS_SENSORS if d.key not in KEYS)


class TestNeverStepsBackwards:
    def test_follows_the_cloud_upwards(self):
        data = {"total": {"solarelectricity": 100.0}}
        sensor = _sensor("solar_total", data)
        assert sensor.native_value == 100.0
        data["total"] = {"solarelectricity": 101.5}
        assert sensor.native_value == 101.5

    def test_a_lower_or_zero_reading_keeps_the_highest(self):
        data = {"total": {"solarelectricity": 100.0}}
        sensor = _sensor("solar_total", data)
        assert sensor.native_value == 100.0
        data["total"] = {"solarelectricity": 0.0}
        assert sensor.native_value == 100.0
        data["total"] = {"solarelectricity": 99.0}
        assert sensor.native_value == 100.0

    def test_a_missing_reading_keeps_the_highest(self):
        data = {"total": {"solarelectricity": 100.0}}
        sensor = _sensor("solar_total", data)
        assert sensor.native_value == 100.0
        data["total"] = {}
        assert sensor.native_value == 100.0

    def test_the_restored_value_is_the_floor_after_a_restart(self):
        data = {"total": {"solarelectricity": 0.0}}
        sensor = _sensor("solar_total", data)
        sensor._highest = 250.0          # what async_added_to_hass restores
        assert sensor.native_value == 250.0
        data["total"] = {"solarelectricity": 260.0}
        assert sensor.native_value == 260.0

    def test_before_the_first_fetch_it_shows_the_restored_value(self):
        sensor = _sensor("solar_total", None)
        sensor._restored_value = 250.0
        assert sensor.native_value == 250.0

    def test_nothing_known_is_unknown(self):
        sensor = _sensor("solar_total", {"total": {}})
        assert sensor.native_value is None
