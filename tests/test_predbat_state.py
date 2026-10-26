"""Tests for predbat_state.read_plan.

Predbat publishes its planned window to entities under the `predbat.` domain
before firing service calls. This module is the only Predbat-aware layer in
the integration; if upstream renames an entity, this is the one file to
patch — and these tests are the regression net.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.ep_cube.predbat_state import (
    PredbatStateError,
    PredbatWindow,
    read_plan,
)


def _set(hass, entity_id: str, state: str, *, timestamp: str | None = None) -> None:
    """Drop a fake state for `entity_id` into the test hass."""
    attrs = {"timestamp": timestamp} if timestamp is not None else {}
    hass.states.async_set(entity_id, state, attrs)


class TestReadPlan:
    async def test_no_predbat_entities_is_no_plan(self, hass):
        # Cold boot — Predbat hasn't published yet, or the user has
        # set_charge_window: False. Must NOT raise.
        plan = read_plan(hass)
        assert plan.charge_enabled is False
        assert plan.charge_window is None
        assert plan.discharge_enabled is False
        assert plan.discharge_window is None
        assert plan.charge_limit_pct is None
        assert plan.discharge_target_soc_pct is None

    async def test_full_charge_plan(self, hass):
        _set(hass, "predbat.best_charge_start", "02:00:00",
             timestamp="2026-05-28T02:00:00+00:00")
        _set(hass, "predbat.best_charge_end", "05:30:00",
             timestamp="2026-05-28T05:30:00+00:00")
        _set(hass, "predbat.best_charge_limit", "90")

        plan = read_plan(hass)
        assert plan.charge_enabled is True
        assert plan.charge_window == PredbatWindow(
            start=datetime(2026, 5, 28, 2, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 28, 5, 30, tzinfo=timezone.utc),
        )
        assert plan.charge_limit_pct == 90

    async def test_full_discharge_plan(self, hass):
        # Upstream calls discharge "export". This name-translation is the
        # whole reason predbat_state exists as a separate module.
        _set(hass, "predbat.best_export_start", "17:30:00",
             timestamp="2026-05-28T17:30:00+00:00")
        _set(hass, "predbat.best_export_end", "19:00:00",
             timestamp="2026-05-28T19:00:00+00:00")
        _set(hass, "predbat.best_export_limit", "20")

        plan = read_plan(hass)
        assert plan.discharge_enabled is True
        assert plan.discharge_window.start == datetime(2026, 5, 28, 17, 30, tzinfo=timezone.utc)
        assert plan.discharge_window.end == datetime(2026, 5, 28, 19, 0, tzinfo=timezone.utc)
        assert plan.discharge_target_soc_pct == 20

    async def test_unavailable_state_is_no_plan(self, hass):
        # `unavailable` shouldn't propagate as a timestamp parse error —
        # it's just "Predbat isn't ready".
        _set(hass, "predbat.best_charge_start", "unavailable")
        _set(hass, "predbat.best_charge_end", "unavailable")
        plan = read_plan(hass)
        assert plan.charge_window is None

    async def test_empty_state_with_null_timestamp_is_no_plan(self, hass):
        # Upstream sets state="" and timestamp=None when nothing planned in
        # the forecast horizon — must surface as no-plan, not an error.
        _set(hass, "predbat.best_charge_start", "", timestamp=None)
        _set(hass, "predbat.best_charge_end", "", timestamp=None)
        plan = read_plan(hass)
        assert plan.charge_window is None
        assert plan.charge_enabled is False

    async def test_collapsed_window_is_no_plan(self, hass):
        # end <= start = degenerate; surface as no-plan rather than a
        # backward window that could trigger an immediate revert.
        _set(hass, "predbat.best_charge_start", "02:00:00",
             timestamp="2026-05-28T02:00:00+00:00")
        _set(hass, "predbat.best_charge_end", "02:00:00",
             timestamp="2026-05-28T02:00:00+00:00")
        plan = read_plan(hass)
        assert plan.charge_window is None

    async def test_naive_timestamp_gets_local_tz(self, hass):
        # If Predbat ever publishes a naive ISO string, we shouldn't crash —
        # _read_timestamp attaches HA's default TZ. Verifies the result is
        # tz-aware so downstream `as_utc` won't blow up.
        _set(hass, "predbat.best_charge_start", "02:00:00",
             timestamp="2026-05-28T02:00:00")
        _set(hass, "predbat.best_charge_end", "05:30:00",
             timestamp="2026-05-28T05:30:00")
        plan = read_plan(hass)
        assert plan.charge_window is not None
        assert plan.charge_window.start.tzinfo is not None
        assert plan.charge_window.end.tzinfo is not None

    async def test_malformed_timestamp_raises(self, hass):
        _set(hass, "predbat.best_charge_start", "02:00:00", timestamp="not-a-date")
        _set(hass, "predbat.best_charge_end", "05:30:00",
             timestamp="2026-05-28T05:30:00+00:00")
        with pytest.raises(PredbatStateError):
            read_plan(hass)

    async def test_non_numeric_limit_returns_none(self, hass):
        # Predbat's limit entity sometimes carries `unavailable` while a
        # window is published — we should log and treat as "no limit known"
        # rather than propagating a parse error.
        _set(hass, "predbat.best_charge_start", "02:00:00",
             timestamp="2026-05-28T02:00:00+00:00")
        _set(hass, "predbat.best_charge_end", "05:30:00",
             timestamp="2026-05-28T05:30:00+00:00")
        _set(hass, "predbat.best_charge_limit", "garbage")
        plan = read_plan(hass)
        assert plan.charge_window is not None
        assert plan.charge_limit_pct is None

    async def test_float_limit_truncates_to_int(self, hass):
        _set(hass, "predbat.best_charge_start", "02:00:00",
             timestamp="2026-05-28T02:00:00+00:00")
        _set(hass, "predbat.best_charge_end", "05:30:00",
             timestamp="2026-05-28T05:30:00+00:00")
        _set(hass, "predbat.best_charge_limit", "89.4")
        plan = read_plan(hass)
        assert plan.charge_limit_pct == 89


class TestChargeRun:
    """The whole charge over back-to-back slots, not just the next slot's end."""

    @staticmethod
    def _publish(hass, *, end, limit, results):
        _set(hass, "predbat.best_charge_start", "00:30:00",
             timestamp="2026-10-27T00:30:00+0000")
        _set(hass, "predbat.best_charge_end", end.split("T")[1],
             timestamp=f"{end}+0000")
        hass.states.async_set("predbat.best_charge_limit", limit,
                              {"results": results} if results is not None else {})

    async def test_run_goes_on_to_the_end_of_the_charge(self, hass):
        # 26 Oct 2026: the sensor said 02:00, the plan charged to 05:30.
        self._publish(hass, end="2026-10-27T02:00:00", limit="100", results={
            "2026-10-26T00:00:00+0000": 0,
            "2026-10-27T00:30:00+0000": 100,
            "2026-10-27T05:30:00+0000": 0,
        })
        plan = read_plan(hass)
        assert plan.charge_window.end == datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc)
        assert plan.charge_run_end == datetime(2026, 10, 27, 5, 30, tzinfo=timezone.utc)
        assert plan.charge_run_limit_pct == 100

    async def test_a_rising_limit_stays_in_the_run_and_sets_the_target(self, hass):
        self._publish(hass, end="2026-10-27T01:00:00", limit="33", results={
            "2026-10-27T00:30:00+0000": 33,
            "2026-10-27T01:00:00+0000": 100,
            "2026-10-27T05:30:00+0000": 0,
        })
        plan = read_plan(hass)
        assert plan.charge_run_end == datetime(2026, 10, 27, 5, 30, tzinfo=timezone.utc)
        assert plan.charge_run_limit_pct == 100

    async def test_a_lower_limit_ends_the_run_at_the_slot_boundary(self, hass):
        self._publish(hass, end="2026-10-27T01:00:00", limit="100", results={
            "2026-10-27T00:30:00+0000": 100,
            "2026-10-27T01:00:00+0000": 20,
            "2026-10-27T05:00:00+0000": 49,
            "2026-10-27T05:30:00+0000": 0,
        })
        plan = read_plan(hass)
        assert plan.charge_run_end == datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc)
        assert plan.charge_run_limit_pct == 100

    async def test_no_curve_means_no_run(self, hass):
        self._publish(hass, end="2026-10-27T02:00:00", limit="100", results=None)
        plan = read_plan(hass)
        assert plan.charge_run_end is None
        assert plan.charge_run_limit_pct is None

    async def test_a_run_with_no_known_end_is_not_guessed(self, hass):
        self._publish(hass, end="2026-10-27T02:00:00", limit="100", results={
            "2026-10-27T00:30:00+0000": 100,
        })
        plan = read_plan(hass)
        assert plan.charge_run_end == datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc)

    async def test_a_zero_first_limit_gives_no_run(self, hass):
        self._publish(hass, end="2026-10-27T02:00:00", limit="0", results={
            "2026-10-27T00:30:00+0000": 0,
        })
        assert read_plan(hass).charge_run_end is None


class TestPlannedEndSoc:
    """The SoC the plan reaches at the end of the export window (the plan page's Limit %)."""

    @staticmethod
    def _publish(hass, *, limit="50.0", results=None, soc_max=20.0):
        hass.states.async_set("predbat.best_export_start", "18:05:00",
                              {"timestamp": "2026-10-15T18:05:00+0100"})
        hass.states.async_set("predbat.best_export_end", "19:00:00",
                              {"timestamp": "2026-10-15T19:00:00+0100"})
        hass.states.async_set("predbat.best_export_limit", limit)
        hass.states.async_set("predbat.best_charge_start", "")
        hass.states.async_set("predbat.best_charge_end", "")
        hass.states.async_set("predbat.best_charge_limit", "100")
        if results is not None:
            hass.states.async_set("predbat.soc_kw_best", "4.0",
                                  {"results": results, "soc_max": soc_max})

    async def test_planned_end_soc_is_read_from_the_soc_curve(self, hass):
        self._publish(hass, results={
            "2026-10-15T18:55:00+0100": 11.664,
            "2026-10-15T19:00:00+0100": 11.141,       # 55.7% of 20 kWh
            "2026-10-15T19:05:00+0100": 11.031,
        })
        plan = read_plan(hass)
        assert plan.discharge_planned_end_soc_pct == 55    # rounded down: 55, not 56
        assert plan.discharge_target_soc_pct == 50         # the programmed limit, kept

    async def test_a_curve_a_hair_under_the_limit_still_reads_as_the_limit(self, hass):
        self._publish(hass, results={"2026-10-15T19:00:00+0100": 10.996})   # 54.98%
        assert read_plan(hass).discharge_planned_end_soc_pct == 55

    async def test_missing_curve_gives_none(self, hass):
        self._publish(hass)                                # no soc_kw_best at all
        assert read_plan(hass).discharge_planned_end_soc_pct is None

    async def test_a_curve_without_a_point_near_the_end_gives_none(self, hass):
        self._publish(hass, results={"2026-10-15T17:00:00+0100": 12.0})
        assert read_plan(hass).discharge_planned_end_soc_pct is None

    async def test_missing_or_bad_capacity_gives_none(self, hass):
        self._publish(hass, results={"2026-10-15T19:00:00+0100": 11.0}, soc_max="n/a")
        assert read_plan(hass).discharge_planned_end_soc_pct is None
        self._publish(hass, results={"2026-10-15T19:00:00+0100": 11.0}, soc_max=0)
        assert read_plan(hass).discharge_planned_end_soc_pct is None

    async def test_the_value_is_clamped_to_0_100(self, hass):
        self._publish(hass, results={"2026-10-15T19:00:00+0100": 25.0}, soc_max=20.0)
        assert read_plan(hass).discharge_planned_end_soc_pct == 100

    async def test_no_export_window_means_no_planned_soc(self, hass):
        hass.states.async_set("predbat.best_export_start", "")
        hass.states.async_set("predbat.best_export_end", "")
        assert read_plan(hass).discharge_planned_end_soc_pct is None
