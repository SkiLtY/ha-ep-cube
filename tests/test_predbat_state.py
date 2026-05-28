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
