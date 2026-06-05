"""Data update coordinator for EP Cube."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import DeviceStatus, EPCubeClient, EPCubeError
from .const import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_STATS_POLL_INTERVAL_SECONDS,
    DOMAIN,
    STATS_SCOPE_ANNUAL,
    STATS_SCOPE_DAILY,
    STATS_SCOPE_MONTHLY,
    STATS_SCOPE_TOTAL,
)
from .repairs import evaluate_predbat_priority_issue

_LOGGER = logging.getLogger(__name__)


class EPCubeCoordinator(DataUpdateCoordinator[DeviceStatus]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: EPCubeClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=DEFAULT_POLL_INTERVAL_SECONDS),
        )
        self.client = client
        self._entry_id = entry.entry_id
        # Cached raw getSwitchMode response. Used by the v1.0 Predbat-priority
        # repair flow to detect user-painted (non-shim) TOU slots that
        # conflict with Predbat's control of the cube. Best-effort: a failed
        # schedule fetch doesn't fail the whole poll (homeDeviceInfo is the
        # critical path); we keep the previous snapshot so a transient
        # network blip doesn't flip the repair flow on and off.
        self._switch_mode: dict[str, Any] | None = None

    @property
    def switch_mode(self) -> dict[str, Any] | None:
        """Latest getSwitchMode snapshot, or None if it's never succeeded."""
        return self._switch_mode

    async def _async_update_data(self) -> DeviceStatus:
        try:
            status = await self.client.get_status()
        except EPCubeError as err:
            raise UpdateFailed(str(err)) from err

        # Schedule fetch is non-critical — log + keep previous cache on failure.
        try:
            self._switch_mode = await self.client.get_switch_mode()
        except EPCubeError as err:
            _LOGGER.debug("get_switch_mode failed (keeping cached): %s", err)

        # Raise / clear the Predbat-priority repair issue based on the
        # latest snapshot. Idempotent — safe to call every tick.
        evaluate_predbat_priority_issue(self.hass, self._entry_id, self._switch_mode)

        return status


# ----------------------------------------------------------------------
# Stats coordinator (Phase 4.2)
# ----------------------------------------------------------------------
# Tick cadence per bucket. The wider windows change much more slowly than
# `today`, so refreshing them every tick would burn cloud calls for no
# user-visible benefit. `yesterday` is special — once frozen, it never
# changes within the day, so we re-fetch only when the HA-local date rolls.
_MONTH_REFRESH = timedelta(hours=1)
_YEAR_REFRESH = timedelta(hours=6)
_TOTAL_REFRESH = timedelta(hours=12)


def _bucket_dates(now: datetime) -> dict[str, str]:
    """Format the queryDateStr values for each bucket from a single timestamp.

    Daily / yesterday use YYYY-MM-DD, monthly YYYY-MM, annual + total YYYY.
    All anchored to HA-local time — the cube's own rollups are presumed
    local-clock-based (it's a homeowner appliance, not a UTC server).
    """
    today = now.date()
    yesterday = today - timedelta(days=1)
    return {
        "today": today.strftime("%Y-%m-%d"),
        "yesterday": yesterday.strftime("%Y-%m-%d"),
        "month": today.strftime("%Y-%m"),
        "year": today.strftime("%Y"),
        "total": today.strftime("%Y"),
    }


class EPCubeStatsCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator for queryDataElectricityV2 rollups.

    Polls today every tick (5 min) and the wider buckets at slower cadences
    to keep the cloud-call budget reasonable. `yesterday` is fetched once on
    startup and once per HA-local date change.

    State shape: a dict keyed by bucket name (`today` / `yesterday` /
    `month` / `year` / `total`), each holding the lowercased fields from
    the cube. Sensors plug in via a `value_fn` that takes the whole dict
    and pulls the right key.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: EPCubeClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_stats_{entry.entry_id}",
            update_interval=timedelta(seconds=DEFAULT_STATS_POLL_INTERVAL_SECONDS),
        )
        self.client = client
        # Per-bucket last-fetch timestamps. Empty on first refresh so each
        # bucket fetches once at startup; subsequent ticks consult these.
        self._last_fetched: dict[str, datetime] = {}
        # Date the cached `yesterday` bucket covers, so we refetch only when
        # the HA-local date rolls. Set after the first successful fetch.
        self._yesterday_date: str | None = None
        # Working state — populated incrementally by each tick. Returned to
        # the coordinator's `data` attribute on every refresh; missing buckets
        # default to {} so sensors can `.get(bucket, {}).get(field)` cleanly.
        self._state: dict[str, dict[str, Any]] = {
            "today": {},
            "yesterday": {},
            "month": {},
            "year": {},
            "total": {},
        }

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        now = dt_util.now()
        dates = _bucket_dates(now)

        # Decide which buckets need a refresh this tick. Always refresh
        # `today`; the rest follow their cadence.
        to_fetch: list[tuple[str, int, str]] = [
            ("today", STATS_SCOPE_DAILY, dates["today"]),
        ]
        if self._yesterday_date != dates["yesterday"]:
            to_fetch.append(("yesterday", STATS_SCOPE_DAILY, dates["yesterday"]))
        if self._is_stale("month", now, _MONTH_REFRESH):
            to_fetch.append(("month", STATS_SCOPE_MONTHLY, dates["month"]))
        if self._is_stale("year", now, _YEAR_REFRESH):
            to_fetch.append(("year", STATS_SCOPE_ANNUAL, dates["year"]))
        if self._is_stale("total", now, _TOTAL_REFRESH):
            to_fetch.append(("total", STATS_SCOPE_TOTAL, dates["total"]))

        # Sequential — concurrent would 4-5× the burst rate against the
        # cube and there's no latency benefit to the user (this is a
        # background coordinator, not a request path).
        for bucket, scope, date_str in to_fetch:
            try:
                result = await self.client.get_stats(scope, date_str)
            except EPCubeError as err:
                # `today` failing is the only critical case — without it the
                # primary sensors go stale. Raise so HA flags the integration.
                # Other buckets are best-effort: keep the prior cached value.
                if bucket == "today":
                    raise UpdateFailed(f"stats today fetch failed: {err}") from err
                _LOGGER.debug("stats %s fetch failed (keeping cached): %s", bucket, err)
                continue
            self._state[bucket] = result
            self._last_fetched[bucket] = now
            if bucket == "yesterday":
                self._yesterday_date = dates["yesterday"]

        return self._state

    def _is_stale(self, bucket: str, now: datetime, max_age: timedelta) -> bool:
        last = self._last_fetched.get(bucket)
        return last is None or (now - last) >= max_age
