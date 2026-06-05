"""Tests for EPCubeStatsCoordinator — queryDataElectricityV2 cadence logic.

The coordinator has 5 buckets (today / yesterday / month / year / total) with
different refresh cadences. These tests verify:
  - First refresh hits all 5 buckets
  - Subsequent ticks within max_age only re-fetch `today`
  - After max_age elapses, the affected wider bucket refreshes
  - When the HA-local date rolls, `yesterday` re-fetches with the new date
  - `today` failure raises UpdateFailed (HA flags the integration)
  - Non-today failures are best-effort (silent, keep cached value)
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ep_cube.api import EPCubeError
from custom_components.ep_cube.coordinator import EPCubeStatsCoordinator


# Same aiohttp daemon-thread filter as test_api_client.py
_orig_enumerate = threading.enumerate


def _filtered_enumerate():
    return [t for t in _orig_enumerate() if "_run_safe_shutdown_loop" not in t.name]


@pytest.fixture(autouse=True)
def filter_aiohttp_shutdown_thread(monkeypatch):
    monkeypatch.setattr(threading, "enumerate", _filtered_enumerate)


# Fixed reference time used by all tests. Anchored at 06:00 UTC (early in
# the UTC day) so that the various T0+Nh patches in the cadence tests stay
# within the same UTC date, except where a test deliberately crosses
# midnight to exercise the day-roll path. The coordinator uses dt_util.now()
# which respects HA's configured TZ; PHCC's default TZ is UTC.
_T0 = datetime(2026, 6, 4, 6, 0, 0, tzinfo=timezone.utc)


def _make_coordinator(hass, *, stats_returns: dict[str, dict] | None = None):
    """Build a coordinator backed by a fake client whose get_stats returns
    a per-scope-type canned dict.

    `stats_returns` maps scopeType (int) → response dict. Calls with an
    unmapped scope return {}.
    """
    returns = stats_returns or {}

    async def _get_stats(scope_type: int, date_str: str) -> dict:
        return dict(returns.get(scope_type, {}))

    client = MagicMock()
    client.get_stats = AsyncMock(side_effect=_get_stats)

    # Coordinator constructor needs a ConfigEntry-shaped object — entry_id
    # is the only attribute it reads.
    entry = MagicMock()
    entry.entry_id = "test_entry"
    coord = EPCubeStatsCoordinator(hass, entry, client)
    return coord, client


@pytest.fixture
def patched_now():
    """Yields a callable that sets dt_util.now()'s return value.

    Use: set(_T0) at test start, then set(_T0 + timedelta(...)) to simulate
    later ticks without real time elapsing.
    """
    current = {"t": _T0}

    def _now():
        return current["t"]

    def _set(t: datetime):
        current["t"] = t

    with patch("custom_components.ep_cube.coordinator.dt_util.now", side_effect=_now):
        yield _set


class TestFirstRefresh:
    async def test_first_refresh_fetches_all_five_buckets(self, hass, patched_now):
        patched_now(_T0)
        coord, client = _make_coordinator(
            hass,
            stats_returns={
                1: {"gridelectricityfrom": 0.44, "gridelectricityto": 7.88},
                2: {"gridelectricityfrom": 16.75},
                3: {"gridelectricityfrom": 33.19},
                0: {"gridelectricityfrom": 33.19},
            },
        )
        data = await coord._async_update_data()

        # 5 buckets requested in first tick.
        assert client.get_stats.await_count == 5

        # Verify each scope appeared with the right date format.
        call_args = [c.args for c in client.get_stats.await_args_list]
        scopes_seen = sorted(scope for scope, _ in call_args)
        assert scopes_seen == [0, 1, 1, 2, 3]  # TOTAL, DAILY×2, MONTHLY, ANNUAL

        # Date formats: daily YYYY-MM-DD, monthly YYYY-MM, annual/total YYYY.
        # _T0 is 2026-06-04 UTC; coordinator uses dt_util.now() so dates derive
        # from the patched value.
        for scope, date_str in call_args:
            if scope == 1:  # daily — today or yesterday
                assert date_str in ("2026-06-04", "2026-06-03")
            elif scope == 2:
                assert date_str == "2026-06"
            elif scope in (0, 3):
                assert date_str == "2026"

        # State exposes all 5 buckets.
        assert set(data.keys()) == {"today", "yesterday", "month", "year", "total"}
        assert data["today"]["gridelectricityfrom"] == 0.44
        assert data["today"]["gridelectricityto"] == 7.88


class TestCadenceLogic:
    async def test_second_tick_within_max_age_only_fetches_today(
        self, hass, patched_now,
    ):
        patched_now(_T0)
        coord, client = _make_coordinator(
            hass, stats_returns={1: {"gridelectricityfrom": 0.44}},
        )
        await coord._async_update_data()  # First refresh: 5 calls.
        client.get_stats.reset_mock()

        # 1 minute later — well within all stale thresholds.
        patched_now(_T0 + timedelta(minutes=1))
        await coord._async_update_data()

        assert client.get_stats.await_count == 1
        scope, date_str = client.get_stats.await_args.args
        assert scope == 1
        assert date_str == "2026-06-04"

    async def test_month_refreshes_after_one_hour(self, hass, patched_now):
        patched_now(_T0)
        coord, client = _make_coordinator(hass)
        await coord._async_update_data()
        client.get_stats.reset_mock()

        # 1h05m later — month should refresh (threshold is 1h).
        patched_now(_T0 + timedelta(hours=1, minutes=5))
        await coord._async_update_data()

        scopes = sorted(c.args[0] for c in client.get_stats.await_args_list)
        # Expect today (1) + month (2). Year + total still cached.
        assert scopes == [1, 2]

    async def test_year_refreshes_after_six_hours(self, hass, patched_now):
        patched_now(_T0)
        coord, client = _make_coordinator(hass)
        await coord._async_update_data()
        client.get_stats.reset_mock()

        # 6h05m later — month, year all stale; total still cached (12h).
        patched_now(_T0 + timedelta(hours=6, minutes=5))
        await coord._async_update_data()

        scopes = sorted(c.args[0] for c in client.get_stats.await_args_list)
        assert scopes == [1, 2, 3]  # today + month + year, not total

    async def test_total_refreshes_after_twelve_hours(self, hass, patched_now):
        patched_now(_T0)
        coord, client = _make_coordinator(hass)
        await coord._async_update_data()
        client.get_stats.reset_mock()

        patched_now(_T0 + timedelta(hours=12, minutes=5))
        await coord._async_update_data()

        scopes = sorted(c.args[0] for c in client.get_stats.await_args_list)
        assert scopes == [0, 1, 2, 3]  # all of them

    async def test_yesterday_refetches_on_date_roll(self, hass, patched_now):
        # Day 1 — initial fetch at T0 = 2026-06-04 06:00 UTC.
        patched_now(_T0)
        coord, client = _make_coordinator(hass)
        await coord._async_update_data()
        client.get_stats.reset_mock()

        # 12 hours later — still 2026-06-04 (18:00 UTC). Yesterday should
        # NOT re-fetch.
        patched_now(_T0 + timedelta(hours=12))
        await coord._async_update_data()
        # The point is: scope=1 should appear exactly ONCE (today, not
        # today+yesterday). Today refreshes every tick; other buckets may
        # also fire depending on cadence — we filter to scope=1 only.
        daily_calls = [
            c.args for c in client.get_stats.await_args_list if c.args[0] == 1
        ]
        assert len(daily_calls) == 1
        assert daily_calls[0] == (1, "2026-06-04")

        # Cross the day boundary — T0 + 19h = 2026-06-05 01:00 UTC.
        client.get_stats.reset_mock()
        patched_now(_T0 + timedelta(hours=19))
        await coord._async_update_data()

        daily_calls = [
            c.args for c in client.get_stats.await_args_list if c.args[0] == 1
        ]
        # Two scope=1 calls now: today (2026-06-05) + yesterday (2026-06-04).
        assert len(daily_calls) == 2
        dates = sorted(date for _, date in daily_calls)
        assert dates == ["2026-06-04", "2026-06-05"]


class TestErrorHandling:
    async def test_today_failure_raises_update_failed(self, hass, patched_now):
        patched_now(_T0)

        async def _get_stats(scope_type, date_str):
            if scope_type == 1 and date_str == "2026-06-04":
                raise EPCubeError("today blew up")
            return {}

        client = MagicMock()
        client.get_stats = AsyncMock(side_effect=_get_stats)
        entry = MagicMock()
        entry.entry_id = "x"
        coord = EPCubeStatsCoordinator(hass, entry, client)

        with pytest.raises(UpdateFailed, match="today"):
            await coord._async_update_data()

    async def test_non_today_failure_keeps_cached_value(
        self, hass, patched_now,
    ):
        # First refresh: month succeeds with value X.
        patched_now(_T0)

        scenario = {"raise_month": False}

        async def _get_stats(scope_type, date_str):
            if scope_type == 2 and scenario["raise_month"]:
                raise EPCubeError("month flaky")
            if scope_type == 2:
                return {"gridelectricityfrom": 16.75}
            return {}

        client = MagicMock()
        client.get_stats = AsyncMock(side_effect=_get_stats)
        entry = MagicMock()
        entry.entry_id = "x"
        coord = EPCubeStatsCoordinator(hass, entry, client)

        data = await coord._async_update_data()
        assert data["month"]["gridelectricityfrom"] == 16.75

        # Now make month fail and tick past its max_age.
        scenario["raise_month"] = True
        patched_now(_T0 + timedelta(hours=2))
        data = await coord._async_update_data()

        # Coordinator did not raise; month bucket still holds the cached value.
        assert data["month"]["gridelectricityfrom"] == 16.75
