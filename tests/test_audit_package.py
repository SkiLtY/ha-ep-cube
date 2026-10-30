"""The example audit package (examples/ha_config/packages/ep_cube_audit.yaml).

It compares the cube's daily grid totals with the figures Octopus bills. The logic is in
Jinja: which day a figure belongs to (local time, so summer time shifts the UTC date), how
the last 12 days are kept in one 255-character helper, and when the alert goes on. These
tests render the package's own templates through Home Assistant against fake states.
"""
from pathlib import Path

import yaml
from homeassistant.helpers.template import Template

PACKAGE = Path(__file__).resolve().parent.parent / "examples" / "ha_config" / "packages" / "ep_cube_audit.yaml"
PKG = yaml.safe_load(PACKAGE.read_text(encoding="utf-8"))


def _auto(auto_id):
    return next(a for a in PKG["automation"] if a["id"] == auto_id)


def _render(hass, text, variables=None):
    return Template(text, hass).async_render(variables or {}, parse_result=False).strip()


def _vars(action_list):
    out = {}
    for step in action_list:
        if "variables" in step:
            out.update(step["variables"])
    return out


async def test_store_builds_the_list(hass):
    hass.states.async_set("input_text.ep_cube_audit_cube_days", "10-26,23.35,0.41;10-27,25.07,0.01")
    hass.states.async_set("sensor.ep_cube_grid_import_yesterday", "21.5")
    hass.states.async_set("sensor.ep_cube_grid_export_yesterday", "0.12")
    auto = _auto("ep_cube_audit_store_cube_days")
    v = _vars(auto["actions"])
    day = "10-28"
    entry = _render(hass, v["entry"], {"day": day})
    assert entry == "10-28,21.5,0.12"
    kept = _render(hass, v["kept"], {"entry": entry})
    assert kept == "10-26,23.35,0.41;10-27,25.07,0.01;10-28,21.5,0.12"


async def test_store_keeps_only_twelve(hass):
    days = ";".join(f"09-{d:02d},10.0,0.0" for d in range(1, 13))
    hass.states.async_set("input_text.ep_cube_audit_cube_days", days)
    v = _vars(_auto("ep_cube_audit_store_cube_days")["actions"])
    kept = _render(hass, v["kept"], {"entry": "10-01,11.0,1.0"})
    parts = kept.split(";")
    assert len(parts) == 12 and parts[0].startswith("09-02") and parts[-1] == "10-01,11.0,1.0"
    assert len(kept) <= 255


async def test_day_follows_local_time(hass):
    await hass.config.async_set_time_zone("Europe/London")
    v = _vars(_auto("ep_cube_audit_import")["actions"])

    def day_for(last_reset):
        class State:
            state = "20"
            attributes = {"last_reset": last_reset}
        return _render(hass, v["day"], {"trigger": type("T", (), {"to_state": State})()})

    assert day_for("2026-10-27T00:00:00+00:00") == "10-27"   # GMT
    assert day_for("2026-10-23T23:00:00+00:00") == "10-24"   # BST: local midnight is 23:00 UTC


async def test_import_compare(hass):
    await hass.config.async_set_time_zone("Europe/London")
    hass.states.async_set("input_text.ep_cube_audit_cube_days", "10-26,23.35,0.41;10-27,25.07,0.01")
    auto = _auto("ep_cube_audit_import")
    v = _vars(auto["actions"])

    class State:
        state = "25.275"
        attributes = {"last_reset": "2026-10-27T00:00:00+00:00"}

    trig = type("T", (), {"to_state": State})()
    variables = {"trigger": trig}
    day = _render(hass, v["day"], variables)
    assert day == "10-27"
    variables["day"] = day
    variables["octopus"] = _render(hass, v["octopus"], variables)
    variables["row"] = _render(hass, v["row"], variables)
    assert variables["row"] == "10-27,25.07,0.01"
    cube = _render(hass, v["cube"], variables)
    assert float(cube) == 25.07
    # a day we never stored: no comparison
    variables["row"] = ""
    assert float(_render(hass, v["cube"], variables)) == 0.0


async def test_export_missing_day_is_minus_one(hass):
    hass.states.async_set("input_text.ep_cube_audit_cube_days", "10-26,23.35,0.41")
    v = _vars(_auto("ep_cube_audit_export")["actions"])
    assert float(_render(hass, v["cube"], {"row": ""})) == -1.0
    assert float(_render(hass, v["cube"], {"row": "10-26,23.35,0.41"})) == 0.41


async def test_sensors_and_alert(hass):
    hass.states.async_set("input_number.ep_cube_audit_threshold", "3")
    hass.states.async_set("input_text.ep_cube_audit_import_result", "10-27|25.275|25.07")
    hass.states.async_set("input_text.ep_cube_audit_export_result", "10-26|0.415|0.41")
    sensors = {s["name"]: s for s in PKG["template"][0]["sensor"]}
    imp = _render(hass, sensors["ep_cube_audit_import_difference"]["state"])
    assert imp == "-0.8"
    hass.states.async_set("sensor.ep_cube_audit_import_difference", imp)
    exp = _render(hass, sensors["ep_cube_audit_export_difference"]["state"])
    assert exp == "-0.01"
    hass.states.async_set("sensor.ep_cube_audit_export_difference", exp)
    alert = PKG["template"][1]["binary_sensor"][0]["state"]
    assert _render(hass, alert) == "False"
    # a glitch day: cube 14.1 against 20.27
    hass.states.async_set("input_text.ep_cube_audit_import_result", "10-25|20.27|14.1")
    imp = _render(hass, sensors["ep_cube_audit_import_difference"]["state"])
    assert imp == "-30.4"
    hass.states.async_set("sensor.ep_cube_audit_import_difference", imp)
    assert _render(hass, alert) == "True"
    # no result yet
    hass.states.async_set("input_text.ep_cube_audit_import_result", "")
    assert _render(hass, sensors["ep_cube_audit_import_difference"]["state"]) == "unknown"
