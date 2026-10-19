"""The example weekly package (examples/ha_config/packages/ep_cube_weekly.yaml).

Its billed-cost automations carry real logic in Jinja (which day a figure belongs
to, how a revised figure is counted, what is ignored), so the templates are
rendered here against a small model of the two helpers they read and write.
The first version used a utility_meter, which only adds increases and so dropped
every day that cost less than the day before.
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


@pytest.fixture(scope="module")
def package() -> dict:
    return yaml.safe_load(PACKAGE.read_text(encoding="utf-8"))


def _automation(package: dict, automation_id: str) -> dict:
    return next(a for a in package["automation"] if a["id"] == automation_id)


class _Model:
    """Runs one add-to-the-week automation against a fake total and last-day text."""

    def __init__(self, hass, automation: dict, direction: str):
        self.hass = hass
        self.automation = automation
        self.direction = direction
        self.total = 0.0
        self.last = ""

    def _render(self, source, variables):
        return Template(source, self.hass).async_render(variables=variables)

    def feed(self, state: str, last_reset: str | None) -> None:
        """Deliver one state change of the Octopus sensor."""
        to_state = {"state": state, "attributes": {"last_reset": last_reset}}
        variables: dict = {"trigger": {"to_state": to_state}}
        for cond in self.automation["conditions"]:
            if not self._render(cond["value_template"], variables):
                return
        actions = self.automation["actions"]
        # the helpers' states as the real automation reads them via states()
        helper_states = {
            f"input_text.ep_cube_week_billed_{self.direction}_last": self.last,
            f"input_number.ep_cube_week_billed_{self.direction}_total": str(self.total),
        }
        self.hass.states.async_set(
            f"input_text.ep_cube_week_billed_{self.direction}_last", helper_states[
                f"input_text.ep_cube_week_billed_{self.direction}_last"] or "unknown")
        self.hass.states.async_set(
            f"input_number.ep_cube_week_billed_{self.direction}_total",
            helper_states[f"input_number.ep_cube_week_billed_{self.direction}_total"])
        for name, source in actions[0]["variables"].items():
            variables[name] = self._render(source, variables)
        if not self._render(actions[1]["value_template"], variables):
            return
        self.total = float(self._render(actions[2]["data"]["value"], variables))
        self.last = str(self._render(actions[3]["data"]["value"], variables))


# Home Assistant's own week runs Monday to Sunday. These tests pretend it is
# Friday 23 Oct 2026, 20:00 in London, so the week began on Monday 19 Oct.
# A figure's last_reset is the start of the day it covers (UTC, BST is +1).
MON = "2026-10-18T23:00:00+00:00"        # Mon 19 Oct
TUE = "2026-10-19T23:00:00+00:00"        # Tue 20 Oct
WED = "2026-10-20T23:00:00+00:00"        # Wed 21 Oct
LAST_SUN = "2026-10-17T23:00:00+00:00"   # Sun 18 Oct: last week


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
        ids = {a["id"] for a in package["automation"]}
        assert ids == {
            "ep_cube_week_billed_import_add",
            "ep_cube_week_billed_export_add",
            "ep_cube_week_billed_reset",
        }

    def test_the_dashboard_sensors_keep_their_entity_ids(self, package):
        names = {s["name"] for s in package["template"][0]["sensor"]}
        assert names == {"ep_cube_week_billed_import", "ep_cube_week_billed_export"}

    def test_the_reset_runs_on_mondays_and_zeroes_both_totals(self, package):
        reset = _automation(package, "ep_cube_week_billed_reset")
        assert reset["conditions"][0]["weekday"] == ["mon"]
        action = reset["actions"][0]
        assert action["data"]["value"] == 0
        assert len(action["target"]["entity_id"]) == 2


class TestBilledImport:
    async def test_every_day_is_added_even_when_it_is_cheaper_than_the_day_before(
        self, friday, package
    ):
        # The real sequence that the utility_meter got wrong: 3.37, then 3.10, then 2.36.
        m = _Model(friday, _automation(package, "ep_cube_week_billed_import_add"), "import")
        m.feed("3.37", MON)
        m.feed("3.10", TUE)
        m.feed("2.36", WED)
        assert m.total == pytest.approx(8.83)

    async def test_a_revised_day_only_adds_the_difference(self, friday, package):
        # Octopus can publish a partial day first (0.33) and the full figure later.
        m = _Model(friday, _automation(package, "ep_cube_week_billed_import_add"), "import")
        m.feed("0.33", MON)
        assert m.total == pytest.approx(0.33)
        m.feed("2.77", MON)
        assert m.total == pytest.approx(2.77)
        m.feed("3.10", TUE)
        assert m.total == pytest.approx(5.87)

    async def test_replaying_a_day_after_a_restart_adds_nothing(self, friday, package):
        m = _Model(friday, _automation(package, "ep_cube_week_billed_import_add"), "import")
        m.feed("3.37", MON)
        m.feed("3.37", MON)
        m.feed("3.37", MON)
        assert m.total == pytest.approx(3.37)

    async def test_last_weeks_days_are_ignored(self, friday, package):
        m = _Model(friday, _automation(package, "ep_cube_week_billed_import_add"), "import")
        m.feed("2.50", LAST_SUN)
        assert m.total == 0.0 and m.last == ""

    @pytest.mark.parametrize("state", ["unknown", "unavailable", "", "n/a"])
    async def test_a_non_numeric_state_is_ignored(self, friday, package, state):
        m = _Model(friday, _automation(package, "ep_cube_week_billed_import_add"), "import")
        m.feed(state, MON)
        assert m.total == 0.0

    async def test_a_missing_last_reset_is_ignored(self, friday, package):
        m = _Model(friday, _automation(package, "ep_cube_week_billed_import_add"), "import")
        m.feed("3.37", None)
        assert m.total == 0.0


class TestBilledExport:
    async def test_export_days_add_up_the_same_way(self, friday, package):
        m = _Model(friday, _automation(package, "ep_cube_week_billed_export_add"), "export")
        m.feed("2.41", MON)
        m.feed("2.82", TUE)
        m.feed("1.10", WED)
        assert m.total == pytest.approx(6.33)
