"""Tests for EPCubePriceCoordinator and EPCubeClient.get_dynamic_prices."""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ep_cube.api import EPCubeClient, EPCubeError
from custom_components.ep_cube.coordinator import EPCubePriceCoordinator

_orig_enumerate = threading.enumerate


def _filtered_enumerate():
    return [t for t in _orig_enumerate() if "_run_safe_shutdown_loop" not in t.name]


@pytest.fixture(autouse=True)
def filter_aiohttp_shutdown_thread(monkeypatch):
    monkeypatch.setattr(threading, "enumerate", _filtered_enumerate)


def _slot(start: str, end: str, price: float) -> dict:
    return {"start": start, "end": end, "price": price}


def _day(date: str, price: float) -> dict:
    return {
        "import": [_slot(f"{date} 00:00:00", f"{date} 00:30:00", price)],
        "export": [_slot(f"{date} 00:00:00", f"{date} 00:30:00", price / 2)],
    }


def _coordinator(hass, client):
    entry = MagicMock()
    entry.entry_id = "e"
    return EPCubePriceCoordinator(hass, entry, client)


class TestCoordinator:
    async def test_before_the_afternoon_only_today_is_fetched(self, hass):
        client = MagicMock()
        client.get_dynamic_prices = AsyncMock(return_value=_day("2026-10-15", 20.0))
        with patch("custom_components.ep_cube.coordinator.dt_util.now",
                   return_value=datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)):
            data = await _coordinator(hass, client)._async_update_data()
        client.get_dynamic_prices.assert_awaited_once_with("2026-10-15")
        assert data["import"][0]["price"] == 20.0 and data["export"][0]["price"] == 10.0

    async def test_just_after_local_midnight_asks_for_the_local_date(self, hass):
        # 00:30 on 17 Oct in summer time is still 16 Oct in UTC: the cloud's own
        # "today" (no date) would answer for the 16th, so the date is always sent.
        client = MagicMock()
        client.get_dynamic_prices = AsyncMock(return_value=_day("2026-10-17", 20.0))
        bst = timezone(timedelta(hours=1))
        with patch("custom_components.ep_cube.coordinator.dt_util.now",
                   return_value=datetime(2026, 10, 17, 0, 30, tzinfo=bst)):
            await _coordinator(hass, client)._async_update_data()
        client.get_dynamic_prices.assert_awaited_once_with("2026-10-17")

    async def test_after_the_afternoon_tomorrow_is_added_and_sorted(self, hass):
        async def prices(date=None):
            return _day("2026-10-16", 30.0) if date == "2026-10-16" else _day("2026-10-15", 20.0)

        client = MagicMock()
        client.get_dynamic_prices = AsyncMock(side_effect=prices)
        with patch("custom_components.ep_cube.coordinator.dt_util.now",
                   return_value=datetime(2026, 10, 15, 16, 30, tzinfo=timezone.utc)):
            data = await _coordinator(hass, client)._async_update_data()
        assert [s["price"] for s in data["import"]] == [20.0, 30.0]
        assert data["import"][0]["start"] < data["import"][1]["start"]

    async def test_slot_times_are_uk_local_and_converted_to_utc(self, hass):
        client = MagicMock()
        client.get_dynamic_prices = AsyncMock(return_value=_day("2026-07-01", 20.0))
        await hass.config.async_set_time_zone("Europe/London")
        with patch("custom_components.ep_cube.coordinator.dt_util.now",
                   return_value=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)):
            data = await _coordinator(hass, client)._async_update_data()
        # 00:00 BST is 23:00 UTC the day before
        assert data["import"][0]["start"] == datetime(2026, 6, 30, 23, 0, tzinfo=timezone.utc)

    async def test_missing_tomorrow_is_not_an_error(self, hass):
        async def prices(date=None):
            if date == "2026-10-16":                  # tomorrow is not published yet
                raise EPCubeError("not published")
            return _day("2026-10-15", 20.0)

        client = MagicMock()
        client.get_dynamic_prices = AsyncMock(side_effect=prices)
        with patch("custom_components.ep_cube.coordinator.dt_util.now",
                   return_value=datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)):
            data = await _coordinator(hass, client)._async_update_data()
        assert len(data["import"]) == 1

    async def test_todays_failure_raises_update_failed(self, hass):
        client = MagicMock()
        client.get_dynamic_prices = AsyncMock(side_effect=EPCubeError("boom"))
        with pytest.raises(UpdateFailed):
            await _coordinator(hass, client)._async_update_data()


class TestClient:
    @pytest.fixture
    def make(self, hass):
        def _make() -> EPCubeClient:
            return EPCubeClient(
                session=async_get_clientsession(hass), base_url="https://mock.invalid",
                dev_id="5613", sg_sn="1", bearer_token="t", api_prefix="/api",
            )
        return _make

    @staticmethod
    def _body(data):
        return {"timestamp": "2026-10-15 10:00:00", "message": "OK", "status": 200, "data": data}

    async def test_parses_slots_and_sends_check_time(self, aioclient_mock, make):
        aioclient_mock.get(
            re.compile(r"^https://mock\.invalid/api/dynamicPrice/calcuPrice\?.*$"),
            json=self._body({
                "prices": [{"priceInc": 26.43, "priceExc": 26.43,
                            "startTime": "2026-10-16 00:00:00", "endTime": "2026-10-16 00:30:00"}],
                "pricesExport": [{"priceInc": 12.87, "startTime": "2026-10-16 00:00:00",
                                  "endTime": "2026-10-16 00:30:00"}, {"bad": 1}],
            }),
        )
        out = await make().get_dynamic_prices("2026-10-16")
        assert out["import"] == [{"start": "2026-10-16 00:00:00", "end": "2026-10-16 00:30:00", "price": 26.43}]
        assert len(out["export"]) == 1 and out["export"][0]["price"] == 12.87
        url = aioclient_mock.mock_calls[0][1]
        assert url.query["devId"] == "5613" and url.query["checkTime"] == "2026-10-16"

    async def test_unpublished_day_gives_empty_lists(self, aioclient_mock, make):
        aioclient_mock.get(
            re.compile(r"^https://mock\.invalid/api/dynamicPrice/calcuPrice\?.*$"),
            json=self._body({"devId": "5613", "unitSmallest": "p"}),
        )
        assert await make().get_dynamic_prices() == {"import": [], "export": []}
