"""The optional EV package (examples/ha_config/packages/ep_cube_ev.yaml).

It only converts the EV charger sensor from watts to kW so the Energy Flow card's
extra bubble reads like the others.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from homeassistant.helpers.template import Template

PACKAGE = (
    Path(__file__).resolve().parent.parent
    / "examples" / "ha_config" / "packages" / "ep_cube_ev.yaml"
)


@pytest.fixture(scope="module")
def sensor() -> dict:
    return yaml.safe_load(PACKAGE.read_text(encoding="utf-8"))["template"][0]["sensor"][0]


def test_it_defines_a_kw_power_sensor(sensor):
    assert sensor["unit_of_measurement"] == "kW"
    assert sensor["device_class"] == "power"
    assert sensor["unique_id"] == "ep_cube_ev_charger_power_kw"


@pytest.mark.parametrize(("watts", "kw"), [("7200", 7.2), ("1620.0", 1.62), ("0", 0.0)])
async def test_it_converts_watts_to_kilowatts(hass, sensor, watts, kw):
    hass.states.async_set("sensor.ep_cube_ev_charger_power", watts)
    assert float(Template(sensor["state"], hass).async_render()) == pytest.approx(kw)


async def test_it_reads_zero_when_the_charger_sensor_is_missing(hass, sensor):
    assert float(Template(sensor["state"], hass).async_render()) == 0.0
