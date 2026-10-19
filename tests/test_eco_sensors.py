"""Eco figures (coal, trees) and Dynamic threshold sensors."""
from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.ep_cube.sensor import SENSORS, STATS_SENSORS

_STATS = {
    "today": {"coal": 11.016, "treenum": 1.5147},
    "year": {"coal": 1268.692, "treenum": 174.44515},
}


def _stats(key):
    return next(d for d in STATS_SENSORS if d.key == key)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("coal_today", 11.016),
        ("coal_year", 1268.692),
        ("trees_today", 1.5147),
        ("trees_year", 174.44515),
    ],
)
def test_eco_values(key, expected):
    assert _stats(key).value_fn(_STATS) == pytest.approx(expected)


@pytest.mark.parametrize("key", ["coal_today", "coal_year", "trees_today", "trees_year"])
def test_eco_unknown_until_fetched(key):
    assert _stats(key).value_fn({}) is None
    assert _stats(key).value_fn({"today": {}, "year": {}}) is None


def test_eco_has_no_device_class_so_nothing_is_converted():
    for key in ("coal_today", "coal_year", "trees_today", "trees_year"):
        assert _stats(key).device_class is None


def test_threshold_sensors_unavailable_outside_dynamic_mode(fake_client):
    import asyncio

    status = asyncio.run(fake_client.get_status())
    sell = next(d for d in SENSORS if d.key == "dynamic_sell_above")
    buy = next(d for d in SENSORS if d.key == "dynamic_buy_below")
    off = replace(status, dynamic_buy_below=None, dynamic_sell_above=None)
    assert buy.available_fn(off) is False
    assert sell.available_fn(off) is False
    on = replace(status, dynamic_buy_below=3.0, dynamic_sell_above=15.0)
    assert buy.available_fn(on) is True and buy.value_fn(on) == 3.0
    assert sell.available_fn(on) is True and sell.value_fn(on) == 15.0
