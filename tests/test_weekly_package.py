"""The example weekly/monthly package (examples/ha_config/packages/ep_cube_weekly.yaml).

Its billed-cost automations carry real logic in Jinja (which day a figure belongs
to, how a revised figure is counted, what is ignored), so a tiny interpreter runs
the automations' own conditions, variables and actions against a model of the
helpers. The first version used a utility_meter, which only adds increases and so
dropped every day that cost less than the day before.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from homeassistant.helpers.template import Template

PACKAGE = (
    Path(__file__).resolve().parent.parent
    / "examples" / "ha_config" / "packages" / "ep_cube_weekly.yaml"
)

IMPORT_ID = "ep_cube_billed_import_add"
EXPORT_ID = "ep_cube_billed_export_add"

W_IMP = "input_number.ep_cube_week_billed_import_total"
W_ENG = "input_number.ep_cube_week_billed_import_energy_total"
W_EXP = "input_number.ep_cube_week_billed_export_total"
M_IMP = "input_number.ep_cube_month_billed_import_total"
M_ENG = "input_number.ep_cube_month_billed_import_energy_total"
M_EXP = "input_number.ep_cube_month_billed_export_total"
LAST_IMP = "input_text.ep_cube_billed_import_last"
LAST_EXP = "input_text.ep_cube_billed_export_last"


@pytest.fixture(scope="module")
def package() -> dict:
    return yaml.safe_load(PACKAGE.read_text(encoding="utf-8"))


def _automation(package: dict, automation_id: str) -> dict:
    return next(a for a in package["automation"] if a["id"] == automation_id)


class _Model:
    """Runs one add-to-the-totals automation against fake helper states."""

    def __init__(self, hass, automation: dict):
        self.hass = hass
        self.automation = automation
        self.values: dict[str, str] = {
            W_IMP: "0", W_ENG: "0", W_EXP: "0", M_IMP: "0", M_ENG: "0", M_EXP: "0",
            LAST_IMP: "", LAST_EXP: "",
        }

    def number(self, entity_id: str) -> float:
        return float(self.values[entity_id])

    def _render(self, source, variables):
        for entity_id, value in self.values.items():
            self.hass.states.async_set(entity_id, value or "unknown")
        return Template(source, self.hass).async_render(variables=variables)

    def _run(self, actions, variables) -> bool:
        for step in actions:
            if "variables" in step:
                for name, source in step["variables"].items():
                    variables[name] = self._render(source, variables)
            elif "condition" in step:
                if not self._render(step["value_template"], variables):
                    return False
            elif "if" in step:
                if all(self._render(c["value_template"], variables) for c in step["if"]):
                    self._run(step["then"], variables)
            elif step.get("action") in ("input_number.set_value", "input_text.set_value"):
                targets = step["target"]["entity_id"]
                value = self._render(step["data"]["value"], variables)
                for entity_id in [targets] if isinstance(targets, str) else targets:
                    self.values[entity_id] = str(value)
        return True

    def feed(self, state: str, last_reset: str | None, energy: float | None = None) -> None:
        """Deliver one state change of the Octopus sensor."""
        attributes = {"last_reset": last_reset}
        if energy is not None:
            attributes["total_without_standing_charge"] = energy
        variables: dict = {"trigger": {"to_state": {"state": state, "attributes": attributes}}}
        for cond in self.automation["conditions"]:
            if not self._render(cond["value_template"], variables):
                return
        self._run(self.automation["actions"], variables)


# These tests pretend it is Friday 23 Oct 2026, 20:00 in London: the week began on
# Monday 19 Oct and the month on 1 Oct. A figure's last_reset is the start of the
# day it covers, in UTC (BST is +1).
MON = "2026-10-18T23:00:00+00:00"        # Mon 19 Oct
TUE = "2026-10-19T23:00:00+00:00"        # Tue 20 Oct
WED = "2026-10-20T23:00:00+00:00"        # Wed 21 Oct
LAST_SUN = "2026-10-17T23:00:00+00:00"   # Sun 18 Oct: last week, this month
EARLY = "2026-10-02T23:00:00+01:00"      # Sat 3 Oct: this month
SEP_END = "2026-09-29T23:00:00+00:00"    # Wed 30 Sep: last month


@pytest.fixture
async def friday(hass, freezer):
    await hass.config.async_set_time_zone("Europe/London")
    freezer.move_to("2026-10-23 19:00:00+00:00")        # 20:00 BST
    return hass


class TestPackageShape:
    def test_it_parses_and_defines_the_expected_pieces(self, package):
        assert {"utility_meter", "input_number", "input_text", "template", "automation"} <= set(package)
        assert "ep_cube_week_billed_import" not in package["utility_meter"], (
            "a utility_meter cannot sum a once-a-day replacing sensor"
        )
        assert {a["id"] for a in package["automation"]} == {
            IMPORT_ID, EXPORT_ID, "ep_cube_billed_week_reset", "ep_cube_billed_month_reset",
        }

    def test_the_dashboard_sensors_exist(self, package):
        names = {s["name"] for s in package["template"][0]["sensor"]}
        assert names == {
            "ep_cube_week_billed_import", "ep_cube_week_billed_import_energy",
            "ep_cube_week_billed_export", "ep_cube_month_billed_import",
            "ep_cube_month_billed_import_energy", "ep_cube_month_billed_export",
        }

    def test_the_month_has_energy_meters_too(self, package):
        month = {k for k in package["utility_meter"] if k.startswith("ep_cube_month_")}
        assert month == {f"ep_cube_month_{n}" for n in ("solar", "import", "export", "consumed")}
        assert all(package["utility_meter"][k]["cycle"] == "monthly" for k in month)

    def test_the_resets(self, package):
        week = _automation(package, "ep_cube_billed_week_reset")
        assert week["conditions"][0]["weekday"] == ["mon"]
        assert len(week["actions"][0]["target"]["entity_id"]) == 3
        month = _automation(package, "ep_cube_billed_month_reset")
        assert "now().day == 1" in month["conditions"][0]["value_template"]
        assert len(month["actions"][0]["target"]["entity_id"]) == 3


class TestBilledImport:
    async def test_every_day_is_added_even_when_it_is_cheaper_than_the_day_before(
        self, friday, package
    ):
        # The sequence the utility_meter got wrong: 3.37, then 3.10, then 2.36.
        m = _Model(friday, _automation(package, IMPORT_ID))
        m.feed("3.37", MON, 2.81)
        m.feed("3.10", TUE, 2.54)
        m.feed("2.36", WED, 1.80)
        assert m.number(W_IMP) == pytest.approx(8.83)
        assert m.number(W_ENG) == pytest.approx(7.15)
        assert m.number(M_IMP) == pytest.approx(8.83)
        assert m.number(M_ENG) == pytest.approx(7.15)

    async def test_the_energy_only_total_falls_back_to_the_full_figure(self, friday, package):
        # A sensor without the attribute: energy-only equals the figure.
        m = _Model(friday, _automation(package, IMPORT_ID))
        m.feed("3.37", MON)
        assert m.number(W_ENG) == pytest.approx(3.37)

    async def test_a_revised_day_only_adds_the_difference(self, friday, package):
        # Octopus can publish a partial day first (0.33) and the full figure later.
        m = _Model(friday, _automation(package, IMPORT_ID))
        m.feed("0.33", MON, 0.30)
        assert m.number(W_IMP) == pytest.approx(0.33)
        m.feed("2.77", MON, 2.21)
        assert m.number(W_IMP) == pytest.approx(2.77)
        assert m.number(W_ENG) == pytest.approx(2.21)
        m.feed("3.10", TUE, 2.54)
        assert m.number(W_IMP) == pytest.approx(5.87)
        assert m.number(M_IMP) == pytest.approx(5.87)

    async def test_replaying_a_day_after_a_restart_adds_nothing(self, friday, package):
        m = _Model(friday, _automation(package, IMPORT_ID))
        for _ in range(3):
            m.feed("3.37", MON, 2.81)
        assert m.number(W_IMP) == pytest.approx(3.37)
        assert m.number(M_IMP) == pytest.approx(3.37)

    async def test_last_weeks_day_counts_for_the_month_but_not_the_week(self, friday, package):
        m = _Model(friday, _automation(package, IMPORT_ID))
        m.feed("2.50", LAST_SUN, 1.94)
        assert m.number(W_IMP) == 0.0 and m.number(W_ENG) == 0.0
        assert m.number(M_IMP) == pytest.approx(2.50)
        assert m.number(M_ENG) == pytest.approx(1.94)

    async def test_an_early_month_day_counts_for_the_month_only(self, friday, package):
        m = _Model(friday, _automation(package, IMPORT_ID))
        m.feed("2.00", EARLY, 1.44)
        assert m.number(W_IMP) == 0.0
        assert m.number(M_IMP) == pytest.approx(2.00)

    async def test_last_months_day_is_ignored(self, friday, package):
        m = _Model(friday, _automation(package, IMPORT_ID))
        m.feed("2.50", SEP_END, 1.94)
        assert m.number(M_IMP) == 0.0 and m.values[LAST_IMP] == ""

    @pytest.mark.parametrize("state", ["unknown", "unavailable", "", "n/a"])
    async def test_a_non_numeric_state_is_ignored(self, friday, package, state):
        m = _Model(friday, _automation(package, IMPORT_ID))
        m.feed(state, MON)
        assert m.number(W_IMP) == 0.0

    async def test_a_missing_last_reset_is_ignored(self, friday, package):
        m = _Model(friday, _automation(package, IMPORT_ID))
        m.feed("3.37", None)
        assert m.number(W_IMP) == 0.0


class TestBilledExport:
    async def test_export_days_add_up_the_same_way(self, friday, package):
        m = _Model(friday, _automation(package, EXPORT_ID))
        m.feed("2.41", MON)
        m.feed("2.82", TUE)
        m.feed("1.10", WED)
        assert m.number(W_EXP) == pytest.approx(6.33)
        assert m.number(M_EXP) == pytest.approx(6.33)

    async def test_a_revised_export_day_adds_only_the_difference(self, friday, package):
        m = _Model(friday, _automation(package, EXPORT_ID))
        m.feed("0.50", MON)
        m.feed("2.41", MON)
        m.feed("2.41", MON)
        assert m.number(W_EXP) == pytest.approx(2.41)

    async def test_last_weeks_export_day_counts_for_the_month_only(self, friday, package):
        m = _Model(friday, _automation(package, EXPORT_ID))
        m.feed("2.00", LAST_SUN)
        assert m.number(W_EXP) == 0.0
        assert m.number(M_EXP) == pytest.approx(2.00)
