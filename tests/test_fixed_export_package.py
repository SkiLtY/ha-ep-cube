"""The example fixed-hours export package (examples/ha_config/packages/ep_cube_fixed_export.yaml).

The logic worth testing is the window length: end minus start, an end after midnight,
and the service's 4-hour cap. The tests render the package's own template through Home
Assistant, and check the services it calls are the ones the integration registers.
"""
from pathlib import Path

import pytest
import yaml
from homeassistant.helpers.template import Template

from custom_components.ep_cube.services import FORCE_EXPORT_MAX_MINUTES

PACKAGE = (
    Path(__file__).resolve().parent.parent
    / "examples" / "ha_config" / "packages" / "ep_cube_fixed_export.yaml"
)
PKG = yaml.safe_load(PACKAGE.read_text(encoding="utf-8"))
AUTOS = {a["id"]: a for a in PKG["automation"]}
START = AUTOS["ep_cube_fixed_export_start"]


def _render(hass, text, variables=None):
    return Template(text, hass).async_render(variables or {}, parse_result=False).strip()


def _minutes(hass, start, end):
    hass.states.async_set("input_datetime.ep_cube_fixed_export_start", start)
    hass.states.async_set("input_datetime.ep_cube_fixed_export_end", end)
    template = next(s for s in START["actions"] if "variables" in s)["variables"]["minutes"]
    return int(_render(hass, template))


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("17:00:00", "19:00:00", 120),
        ("17:00:00", "17:30:00", 30),
        ("22:00:00", "01:00:00", 180),      # past midnight
        ("17:00:00", "23:00:00", 240),      # six hours is cut to the service's limit
        ("17:00:00", "17:00:00", 0),        # no window: the automation skips it
    ],
)
async def test_window_length(hass, start, end, expected):
    assert _minutes(hass, start, end) == expected


def test_the_cap_matches_the_service_limit():
    assert FORCE_EXPORT_MAX_MINUTES == 240


def test_it_calls_the_supported_services_with_their_required_fields():
    call = next(s for s in START["actions"] if s.get("action") == "ep_cube.force_export")
    assert set(call["data"]) == {"duration_minutes", "reserve_pct"}
    stop = AUTOS["ep_cube_fixed_export_stop"]["actions"]
    assert stop == [{"action": "ep_cube.force_export_stop"}]


def test_it_only_starts_when_enabled_and_above_the_minimum():
    conditions = {c["entity_id"]: c for c in START["conditions"]}
    assert conditions["input_boolean.ep_cube_fixed_export_enabled"]["state"] == "on"
    assert conditions["sensor.ep_cube_battery_soc"]["above"] == "input_number.ep_cube_fixed_export_min_soc"


def test_every_helper_the_automations_use_is_defined():
    defined = {
        f"{domain}.{key}"
        for domain in ("input_boolean", "input_datetime", "input_number")
        for key in PKG[domain]
    }
    text = PACKAGE.read_text(encoding="utf-8")
    used = {
        word.strip(" \"',}:")
        for word in text.replace("(", " ").replace(")", " ").split()
        if word.strip(" \"',}:").startswith(
            ("input_boolean.ep_cube_fixed", "input_datetime.ep_cube_fixed", "input_number.ep_cube_fixed")
        )
    }
    assert used and used <= defined
