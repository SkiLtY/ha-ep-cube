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
ROLLUP_ID = "ep_cube_energy_rollup"
KINDS = ("solar", "import", "export", "consumed")

W_IMP = "input_number.ep_cube_week_billed_import_total"
W_ENG = "input_number.ep_cube_week_billed_import_energy_total"
W_EXP = "input_number.ep_cube_week_billed_export_total"
M_IMP = "input_number.ep_cube_month_billed_import_total"
M_ENG = "input_number.ep_cube_month_billed_import_energy_total"
M_EXP = "input_number.ep_cube_month_billed_export_total"
STANDING = "input_number.ep_cube_standing_charge"
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
            W_IMP: "0", W_ENG: "0", W_EXP: "0", M_IMP: "0", M_ENG: "0", M_EXP: "0", STANDING: "0",
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
        assert {"input_number", "input_text", "input_boolean", "template", "automation"} <= set(package)
        assert "utility_meter" not in package, (
            "a utility_meter treats every dip in a cloud counter as a reset and over-counts"
        )
        assert {a["id"] for a in package["automation"]} == {
            IMPORT_ID, EXPORT_ID, ROLLUP_ID, "ep_cube_billed_week_reset", "ep_cube_billed_month_reset",
        }

    def test_the_month_chart_checkbox_helper_exists(self, package):
        assert "ep_cube_show_standing_charge" in package["input_boolean"]

    def test_the_dashboard_sensors_exist(self, package):
        names = {s["name"] for s in package["template"][0]["sensor"]}
        energy = {f"ep_cube_{per}_{k}" for per in ("week", "month") for k in KINDS}
        assert names == energy | {
            "ep_cube_week_billed_import", "ep_cube_week_billed_import_energy",
            "ep_cube_week_billed_export", "ep_cube_month_billed_import",
            "ep_cube_month_billed_import_energy", "ep_cube_month_billed_export",
        }

    def test_the_resets(self, package):
        week = _automation(package, "ep_cube_billed_week_reset")
        assert week["conditions"][0]["weekday"] == ["mon"]
        week_targets = week["actions"][0]["target"]["entity_id"]
        assert len(week_targets) == 7
        assert "input_number.ep_cube_week_consumed_total" in week_targets
        month = _automation(package, "ep_cube_billed_month_reset")
        assert "now().day == 1" in month["conditions"][0]["value_template"]
        assert len(month["actions"][0]["target"]["entity_id"]) == 7


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

    async def test_the_same_day_in_another_notation_adds_nothing(self, friday, package):
        """After an Octopus reload the sensor handed over the same day's last_reset as
        local time with a space ("2026-10-19 00:00:00+01:00") instead of UTC ISO, and the
        old text comparison counted the day again (20 Oct 2026: two extra adds of 18 Oct)."""
        m = _Model(friday, _automation(package, IMPORT_ID))
        m.feed("3.37", MON, 2.81)
        m.feed("3.37", "2026-10-19 00:00:00+01:00", 2.81)
        m.feed("3.37", MON, 2.81)
        assert m.number(W_IMP) == pytest.approx(3.37)
        assert m.number(M_IMP) == pytest.approx(3.37)
        assert m.number(M_ENG) == pytest.approx(2.81)

    async def test_the_same_export_day_in_another_notation_adds_nothing(self, friday, package):
        m = _Model(friday, _automation(package, "ep_cube_billed_export_add"))
        m.feed("1.78", MON)
        m.feed("1.78", "2026-10-19 00:00:00+01:00")
        m.feed("1.78", MON)
        assert m.number("input_number.ep_cube_month_billed_export_total") == pytest.approx(1.78)

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


# ----------------------------------------------------------------------
# Energy totals: finished days added once, plus today's live figure
# ----------------------------------------------------------------------
def _total(per: str, kind: str) -> str:
    return f"input_number.ep_cube_{per}_{kind}_total"


YESTERDAY = {
    "solar": "sensor.ep_cube_solar_yesterday",
    "import": "sensor.ep_cube_grid_import_yesterday",
    "export": "sensor.ep_cube_grid_export_yesterday",
    "consumed": "sensor.ep_cube_backup_loads_yesterday",
}
TODAY = {
    "solar": "sensor.ep_cube_solar_today",
    "import": "sensor.ep_cube_grid_import_today",
    "export": "sensor.ep_cube_grid_export_today",
    "consumed": "sensor.ep_cube_backup_loads_today",
}
LAST_DAY = "input_text.ep_cube_energy_last_day"


class _Rollup:
    """Runs the energy roll-up automation: the hourly check, with yesterday's figures set."""

    def __init__(self, hass, automation: dict):
        self.hass = hass
        self.automation = automation
        self.values = {_total(p, k): "0" for p in ("week", "month") for k in KINDS}
        self.values[LAST_DAY] = ""

    def number(self, per: str, kind: str) -> float:
        return float(self.values[_total(per, kind)])

    def _render(self, source, variables):
        for entity_id, value in self.values.items():
            self.hass.states.async_set(entity_id, value or "unknown")
        return Template(source, self.hass).async_render(variables=variables)

    def _run(self, actions, variables) -> None:
        for step in actions:
            if "variables" in step:
                for name, source in step["variables"].items():
                    variables[name] = self._render(source, variables)
            elif "if" in step:
                if all(self._render(c["value_template"], variables) for c in step["if"]):
                    self._run(step["then"], variables)
            elif step.get("action") in ("input_number.set_value", "input_text.set_value"):
                targets = step["target"]["entity_id"]
                value = self._render(step["data"]["value"], variables)
                for entity_id in [targets] if isinstance(targets, str) else targets:
                    self.values[entity_id] = str(value)

    def tick(self, **yesterday: float) -> None:
        for kind, sensor in YESTERDAY.items():
            self.hass.states.async_set(sensor, str(yesterday.get(kind, 0)))
        variables: dict = {}
        for cond in self.automation["conditions"]:
            if not self._render(cond["value_template"], variables):
                return
        self._run(self.automation["actions"], variables)


class TestEnergyRollup:
    DAY = {"solar": 10.5, "import": 16.0, "export": 6.3, "consumed": 22.3}

    async def test_yesterday_is_added_to_the_week_and_month(self, friday, package):
        r = _Rollup(friday, _automation(package, ROLLUP_ID))
        r.tick(**self.DAY)
        for kind, value in self.DAY.items():
            assert r.number("week", kind) == pytest.approx(value)
            assert r.number("month", kind) == pytest.approx(value)
        assert r.values[LAST_DAY] == "2026-10-22"

    async def test_the_same_day_is_never_added_twice(self, friday, package):
        r = _Rollup(friday, _automation(package, ROLLUP_ID))
        for _ in range(4):                      # the hourly check, plus restarts
            r.tick(**self.DAY)
        assert r.number("month", "consumed") == pytest.approx(22.3)
        assert r.number("week", "solar") == pytest.approx(10.5)

    async def test_a_dip_in_a_cloud_counter_cannot_inflate_the_total(self, friday, package):
        # What the utility_meter got wrong: only the day's final figure is ever added.
        r = _Rollup(friday, _automation(package, ROLLUP_ID))
        r.tick(**{**self.DAY, "consumed": 22.3})
        assert r.number("month", "consumed") == pytest.approx(22.3)

    async def test_it_waits_until_yesterdays_figures_have_arrived(self, friday, package):
        r = _Rollup(friday, _automation(package, ROLLUP_ID))
        r.tick()                                # the cloud has not rolled over yet: all zeros
        assert r.number("month", "consumed") == 0.0 and r.values[LAST_DAY] == ""

    async def test_a_day_before_this_week_still_counts_for_the_month(self, hass, freezer, package):
        await hass.config.async_set_time_zone("Europe/London")
        freezer.move_to("2026-10-19 08:30:00+00:00")     # Monday 09:30: yesterday was Sunday 18 Oct
        r = _Rollup(hass, _automation(package, ROLLUP_ID))
        r.tick(**self.DAY)
        assert r.number("week", "consumed") == 0.0
        assert r.number("month", "consumed") == pytest.approx(22.3)

    async def test_a_day_before_this_week_and_month_counts_for_neither(self, hass, freezer, package):
        await hass.config.async_set_time_zone("Europe/London")
        freezer.move_to("2027-03-01 08:30:00+00:00")     # a Monday and the 1st: yesterday was Sunday 28 Feb
        r = _Rollup(hass, _automation(package, ROLLUP_ID))
        r.tick(**self.DAY)
        assert r.number("week", "consumed") == 0.0 and r.number("month", "consumed") == 0.0

    async def test_it_does_not_run_before_one_in_the_morning(self, hass, freezer, package):
        await hass.config.async_set_time_zone("Europe/London")
        freezer.move_to("2026-10-23 23:20:00+00:00")     # 00:20 BST, the cloud may not have rolled over
        r = _Rollup(hass, _automation(package, ROLLUP_ID))
        r.tick(**self.DAY)
        assert r.number("month", "consumed") == 0.0


class TestWeekAndMonthSensors:
    @staticmethod
    def _sensor(package, name: str) -> str:
        return next(s["state"] for s in package["template"][0]["sensor"] if s["name"] == name)

    def _render(self, hass, package, name: str, states: dict[str, str]):
        for entity_id, value in states.items():
            hass.states.async_set(entity_id, value)
        return float(Template(self._sensor(package, name), hass).async_render())

    async def test_energy_is_the_finished_days_plus_todays_live_figure(self, friday, package):
        for per in ("week", "month"):
            for kind in KINDS:
                value = self._render(friday, package, f"ep_cube_{per}_{kind}", {
                    _total(per, kind): "100.0", TODAY[kind]: "7.5"})
                assert value == pytest.approx(107.5)

    async def test_the_standing_charge_for_the_unbilled_day_is_counted(self, hass, freezer, package):
        # Monday 19 Oct, morning: Octopus has billed up to Saturday 17 Oct, and its own page
        # already counts Sunday's standing charge.
        await hass.config.async_set_time_zone("Europe/London")
        freezer.move_to("2026-10-19 08:30:00+00:00")
        states = {
            "input_text.ep_cube_billed_import_last": "2026-10-16T23:00:00+00:00|2.36|1.81",
            "input_number.ep_cube_standing_charge": "0.56",
            _total("month", "solar"): "0",
            "input_number.ep_cube_month_billed_import_total": "44.66",
        }
        assert self._render(hass, package, "ep_cube_month_billed_import", states) == pytest.approx(45.22)

    async def test_no_standing_charge_is_added_once_everything_is_billed(self, hass, freezer, package):
        await hass.config.async_set_time_zone("Europe/London")
        freezer.move_to("2026-10-19 08:30:00+00:00")
        states = {
            "input_text.ep_cube_billed_import_last": "2026-10-17T23:00:00+00:00|1.90|1.34",   # Sun 18 Oct
            "input_number.ep_cube_standing_charge": "0.56",
            "input_number.ep_cube_month_billed_import_total": "46.56",
        }
        assert self._render(hass, package, "ep_cube_month_billed_import", states) == pytest.approx(46.56)

    async def test_the_week_counts_pending_days_from_monday(self, friday, package):
        # Friday 23 Oct 20:00: billed up to Wednesday 21 Oct, so Thursday's standing charge is pending.
        states = {
            "input_text.ep_cube_billed_import_last": "2026-10-20T23:00:00+00:00|2.36|1.81",
            "input_number.ep_cube_standing_charge": "0.56",
            "input_number.ep_cube_week_billed_import_total": "7.00",
        }
        assert self._render(friday, package, "ep_cube_week_billed_import", states) == pytest.approx(7.56 + 0.56 - 0.56 + 0.0)

    async def test_an_empty_week_so_far_counts_the_days_gone_by(self, hass, freezer, package):
        # Wednesday 21 Oct 09:30 and nothing billed yet this week: Monday and Tuesday are pending.
        await hass.config.async_set_time_zone("Europe/London")
        freezer.move_to("2026-10-21 08:30:00+00:00")
        states = {
            "input_text.ep_cube_billed_import_last": "2026-10-17T23:00:00+00:00|2.36|1.81",    # Sun 18 Oct
            "input_number.ep_cube_standing_charge": "0.56",
            "input_number.ep_cube_week_billed_import_total": "0",
        }
        assert self._render(hass, package, "ep_cube_week_billed_import", states) == pytest.approx(1.12)

    async def test_the_energy_only_figures_are_unchanged(self, friday, package):
        value = self._render(friday, package, "ep_cube_month_billed_import_energy", {
            "input_number.ep_cube_month_billed_import_energy_total": "34.65"})
        assert value == pytest.approx(34.65)
