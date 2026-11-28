"""The example price-triggered export package (examples/ha_config/packages/ep_cube_price_export.yaml).

The logic worth testing is the price sensor (finding the Octopus export rate and
turning GBP into pence) and the window length (until one minute into the next half
hour). The tests render the package's own templates through Home Assistant, and check
the services it calls are the ones the integration registers.
"""
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

PACKAGE = (
    Path(__file__).resolve().parent.parent
    / "examples" / "ha_config" / "packages" / "ep_cube_price_export.yaml"
)
PKG = yaml.safe_load(PACKAGE.read_text(encoding="utf-8"))
AUTOS = {a["id"]: a for a in PKG["automation"]}
START = AUTOS["ep_cube_price_export_start"]
PRICE = PKG["template"][0]["sensor"][0]
OCTOPUS = "sensor.octopus_energy_electricity_19l0000000_2200000000000_export_current_rate"


def _render(hass, text, variables=None):
    return Template(text, hass).async_render(variables or {}, parse_result=False).strip()


async def test_price_is_the_octopus_export_rate_in_pence(hass):
    hass.states.async_set(OCTOPUS, "0.3083", {"unit_of_measurement": "GBP/kWh"})
    hass.states.async_set(OCTOPUS.replace("export_current", "export_next"), "0.10")
    assert float(_render(hass, PRICE["state"])) == pytest.approx(30.83)
    assert _render(hass, PRICE["availability"]) == "True"


async def test_import_rate_is_not_mistaken_for_export(hass):
    hass.states.async_set(OCTOPUS.replace("_export_current_rate", "_current_rate"), "0.36")
    assert _render(hass, PRICE["availability"]) == "False"


async def test_unavailable_without_a_number(hass):
    hass.states.async_set(OCTOPUS, "unavailable")
    assert _render(hass, PRICE["availability"]) == "False"


@pytest.mark.parametrize(
    ("minute", "expected"),
    [
        (1, 30),     # :01 trigger -> until :31
        (31, 30),    # :31 trigger -> until :01
        (17, 14),    # switch turned on at :17 -> until :31
        (0, 1),      # switch turned on at :00 -> until :01
        (45, 16),    # switch turned on at :45 -> until :01
    ],
)
async def test_window_runs_to_one_minute_into_the_next_half_hour(hass, minute, expected):
    template = next(s for s in START["actions"] if "variables" in s)["variables"]["minutes"]
    fake_now = datetime(2026, 11, 25, 16, minute, 0, tzinfo=dt_util.UTC)
    with patch("homeassistant.helpers.template.dt_util.now", return_value=fake_now):
        assert int(_render(hass, template)) == expected


def test_it_checks_at_one_minute_into_each_half_hour():
    minutes = {t["minutes"] for t in START["triggers"] if t["trigger"] == "time_pattern"}
    assert minutes == {1, 31}


def test_it_calls_the_supported_services_with_their_required_fields():
    call = next(s for s in START["actions"] if s.get("action") == "ep_cube.force_export")
    assert set(call["data"]) == {"duration_minutes", "reserve_pct"}
    stop = AUTOS["ep_cube_price_export_stop"]["actions"]
    assert stop == [{"action": "ep_cube.force_export_stop"}]


async def test_price_condition(hass):
    cond = next(
        c for c in START["conditions"]
        if c["condition"] == "template"
    )["value_template"]
    hass.states.async_set("input_number.ep_cube_price_export_threshold", "25")
    for price, expected in (("30.8", "True"), ("25.0", "True"), ("18.2", "False")):
        hass.states.async_set("sensor.ep_cube_export_price", price)
        assert _render(hass, cond) == expected
    hass.states.async_set("input_number.ep_cube_price_export_threshold", "unavailable")
    assert _render(hass, cond) == "False"


def test_it_only_starts_when_enabled_and_above_the_minimum():
    conditions = {c.get("entity_id"): c for c in START["conditions"]}
    assert conditions["input_boolean.ep_cube_price_export_enabled"]["state"] == "on"
    assert conditions["sensor.ep_cube_battery_soc"]["above"] == "input_number.ep_cube_price_export_min_soc"


def test_every_helper_the_automations_use_is_defined():
    defined = {
        f"{domain}.{key}"
        for domain in ("input_boolean", "input_number")
        for key in PKG[domain]
    }
    text = PACKAGE.read_text(encoding="utf-8")
    used = {
        word.strip(" \"',}:")
        for word in text.replace("(", " ").replace(")", " ").split()
        if word.strip(" \"',}:").startswith(
            ("input_boolean.ep_cube_price", "input_number.ep_cube_price")
        )
    }
    assert used and used <= defined
