"""The Time of Use schedule read from getSwitchMode (issue #26)."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.ep_cube.api import DeviceStatus
from custom_components.ep_cube.tou_schedule import (
    BAND_MID_PEAK,
    BAND_NONE,
    BAND_OFF_PEAK,
    BAND_PEAK,
    band_at,
    next_change,
    parse_schedule,
    parse_slot,
    period_rows,
)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    """A local time on a chosen weekday: 2026-11-23 is a Monday (day 1)."""
    return datetime(2026, 11, 22 + day, hour, minute)


def _mode(**overrides):
    base = {
        "offPeakTimeList": ["00:30_04:30_0.05"],
        "midPeakTimeList": ["04:30_16:00_0.25"],
        "peakTimeList": ["16:00_19:00_0.40"],
        "activeWeek": [1, 2, 3, 4, 5],
        "offPeakTimeListNonWorkDay": [],
        "midPeakTimeListNonWorkDay": [],
        "peakTimeListNonWorkDay": [],
        "activeWeekNonWorkDay": [6, 7],
        "dayLightSavingTime": False,
        "dayLightOffPeakTimeList": [],
        "dayLightMidPeakTimeList": [],
        "dayLightPeakTimeList": [],
        "dayLightActiveWeek": [1, 2, 3, 4, 5],
        "dayLightOffPeakTimeListNonWorkDay": [],
        "dayLightMidPeakTimeListNonWorkDay": [],
        "dayLightPeakTimeListNonWorkDay": [],
        "dayLightActiveWeekNonWorkDay": [6, 7],
    }
    base.update(overrides)
    return base


class TestParse:
    def test_a_slot_string_is_split_into_minutes_and_price(self):
        slot = parse_slot(BAND_OFF_PEAK, "00:30_04:30_0.05")
        assert (slot.start, slot.end, slot.price) == (30, 270, 0.05)

    @pytest.mark.parametrize("raw", [None, 5, "", "garbage", "25:00_26:00_1", "10:00_xx_1"])
    def test_a_malformed_slot_is_skipped(self, raw):
        assert parse_slot(BAND_PEAK, raw) is None

    def test_a_missing_price_is_allowed(self):
        assert parse_slot(BAND_PEAK, "10:00_11:00").price is None

    def test_no_schedule_keys_means_no_schedule(self):
        assert parse_schedule({"workStatus": "1"}) is None
        assert parse_schedule({}) is None

    def test_the_real_cloud_fixture_parses(self, get_switch_mode):
        schedule = parse_schedule(get_switch_mode)
        assert schedule is not None
        rows = period_rows(schedule)
        assert {r["band"] for r in rows} == {BAND_OFF_PEAK, BAND_MID_PEAK, BAND_PEAK}
        assert {"days": "work_days", "band": BAND_PEAK, "start": "16:00", "end": "19:00", "price": 0.4} in rows

    def test_the_clock_change_lists_are_used_while_that_flag_is_on(self):
        mode = _mode(
            dayLightSavingTime=True,
            dayLightOffPeakTimeList=["01:30_05:30_0.09"],
            dayLightActiveWeek=[1, 2, 3, 4, 5, 6, 7],
        )
        schedule = parse_schedule(mode)
        assert schedule.dst_active
        assert [(s.start, s.end) for s in schedule.work_days.slots] == [(90, 330)]
        assert schedule.work_days.days == frozenset(range(1, 8))


class TestBandAt:
    def setup_method(self):
        self.schedule = parse_schedule(_mode())

    @pytest.mark.parametrize(
        ("hour", "minute", "band"),
        [
            (0, 0, None),            # before the first slot
            (0, 30, BAND_OFF_PEAK),  # a slot starts on its first minute
            (4, 29, BAND_OFF_PEAK),
            (4, 30, BAND_MID_PEAK),  # and ends before its last
            (16, 0, BAND_PEAK),
            (18, 59, BAND_PEAK),
            (19, 0, None),
        ],
    )
    def test_weekday_bands(self, hour, minute, band):
        slot = band_at(self.schedule, _at(1, hour, minute))
        assert (slot.band if slot else None) == band

    def test_a_non_work_day_with_no_slots_has_no_band(self):
        assert band_at(self.schedule, _at(6, 17)) is None

    def test_a_day_no_set_claims_has_no_band(self):
        mode = _mode(activeWeek=[1, 2, 3], activeWeekNonWorkDay=[6, 7])
        assert band_at(parse_schedule(mode), _at(4, 17)) is None  # Thursday

    def test_a_slot_that_crosses_midnight_covers_both_sides(self):
        schedule = parse_schedule(
            _mode(offPeakTimeList=["23:30_05:30_0.07"], midPeakTimeList=[], peakTimeList=[])
        )
        assert band_at(schedule, _at(1, 23, 45)).band == BAND_OFF_PEAK
        assert band_at(schedule, _at(2, 2, 0)).band == BAND_OFF_PEAK   # the next morning
        assert band_at(schedule, _at(2, 5, 30)) is None
        assert band_at(schedule, _at(2, 23, 0)) is None

    def test_after_midnight_follows_the_day_the_slot_started_on(self):
        # Friday's late slot runs into Saturday morning even though Saturday has no slots.
        schedule = parse_schedule(
            _mode(offPeakTimeList=["23:30_05:30_0.07"], midPeakTimeList=[], peakTimeList=[])
        )
        assert band_at(schedule, _at(6, 2, 0)).band == BAND_OFF_PEAK
        # Sunday morning follows Saturday, which has no slot.
        assert band_at(schedule, _at(7, 2, 0)) is None

    def test_an_end_of_day_slot_runs_to_midnight(self):
        schedule = parse_schedule(
            _mode(offPeakTimeList=[], midPeakTimeList=["00:00_24:00_0.2"], peakTimeList=[])
        )
        assert band_at(schedule, _at(1, 23, 59)).band == BAND_MID_PEAK


class TestNextChange:
    def test_the_next_edge_with_a_different_band(self):
        schedule = parse_schedule(_mode())
        moment, band = next_change(schedule, _at(1, 2, 0))
        assert (moment.hour, moment.minute, band) == (4, 30, BAND_MID_PEAK)

    def test_leaving_the_last_slot_goes_to_none(self):
        schedule = parse_schedule(_mode())
        moment, band = next_change(schedule, _at(1, 17, 0))
        assert (moment.hour, band) == (19, BAND_NONE)

    def test_it_looks_across_midnight_and_over_a_weekend(self):
        schedule = parse_schedule(_mode())
        # Friday 20:00: nothing until Monday 00:30, since the weekend has no slots.
        moment, band = next_change(schedule, _at(5, 20, 0))
        assert (moment.isoweekday(), moment.hour, moment.minute, band) == (1, 0, 30, BAND_OFF_PEAK)

    def test_an_empty_schedule_has_no_next_change(self):
        schedule = parse_schedule(
            _mode(offPeakTimeList=[], midPeakTimeList=[], peakTimeList=[])
        )
        assert next_change(schedule, _at(1, 12, 0)) is None

    def test_one_band_all_day_never_changes(self):
        schedule = parse_schedule(
            _mode(offPeakTimeList=[], midPeakTimeList=["00:00_24:00_0.2"], peakTimeList=[],
                  activeWeek=[1, 2, 3, 4, 5, 6, 7], activeWeekNonWorkDay=[])
        )
        assert next_change(schedule, _at(1, 12, 0)) is None


class TestSensor:
    @pytest.fixture
    async def entry(self, hass, mock_config_entry, fake_client, get_switch_mode):
        status = await fake_client.get_status()
        fake_client.get_status = AsyncMock(
            return_value=_with_schedule(status, parse_schedule(get_switch_mode))
        )
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        with (
            patch("custom_components.ep_cube.EPCubeClient", return_value=fake_client),
            patch(
                "custom_components.ep_cube.make_reauth_callback",
                return_value=AsyncMock(return_value=None),
            ),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            yield entry
            await hass.config_entries.async_unload(entry.entry_id)

    def _entity_id(self, hass, entry):
        registry = er.async_get(hass)
        return registry.async_get_entity_id("sensor", "ep_cube", f"{entry.entry_id}_tou_schedule")

    async def test_the_state_is_the_band_now_with_periods_as_attributes(self, hass, entry):
        entity_id = self._entity_id(hass, entry)
        assert entity_id is not None
        with patch.object(dt_util, "now", return_value=dt_util.as_local(datetime(2026, 11, 23, 17, 0))):
            coordinator = hass.data["ep_cube"][entry.entry_id]["coordinator"]
            await coordinator.async_refresh()
            await hass.async_block_till_done()
            state = hass.states.get(entity_id)
        assert state.state == BAND_PEAK
        attrs = state.attributes
        assert attrs["schedule_in_use"] is False   # the fake cube is in self-consumption
        assert attrs["clock_change_set"] is False
        assert attrs["current_price"] == 0.4
        assert attrs["next_band"] == BAND_NONE
        assert {p["band"] for p in attrs["periods"]} == {BAND_OFF_PEAK, BAND_MID_PEAK, BAND_PEAK}

    async def test_it_is_unavailable_when_the_cloud_sends_no_schedule(
        self, hass, mock_config_entry, fake_client
    ):
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        with (
            patch("custom_components.ep_cube.EPCubeClient", return_value=fake_client),
            patch(
                "custom_components.ep_cube.make_reauth_callback",
                return_value=AsyncMock(return_value=None),
            ),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            state = hass.states.get(self._entity_id(hass, entry))
            assert state.state == "unavailable"
            await hass.config_entries.async_unload(entry.entry_id)


def _with_schedule(status: DeviceStatus, schedule) -> DeviceStatus:
    import dataclasses

    return dataclasses.replace(status, tou_schedule=schedule)
