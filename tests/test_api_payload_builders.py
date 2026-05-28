"""Tests for the switchMode payload builders.

These functions are load-bearing: the cube rejects "minimal" 3-field calls
with `500 "The parameter cannot be null ：weatherWatch"`, so every required
key must be present on every override write. We also assert the type-coercion
rules (day arrays as list[str], booleans as native bool) that the cube enforces
silently — a list[int] for activeWeek comes back as a misleading
`403 "token expired"`.
"""
from __future__ import annotations

import pytest

from custom_components.ep_cube.api import (
    build_slot,
    build_switch_mode_payload,
    payload_from_switch_mode_read,
)

# Every required field per docs/PHASE_3_2.md. Missing any of these will make
# the cloud 500. Locked here as a regression-net against future "let's
# trim the payload" temptations.
REQUIRED_KEYS: frozenset[str] = frozenset({
    "devId",
    "workStatus",
    "allowChargingXiaGrid",
    "weatherWatch",
    "onlySave",
    "selfConsumptioinReserveSoc",
    "backupPowerReserveSoc",
    "touType",
    "dayLightSavingTime",
    "peakTimeList",
    "midPeakTimeList",
    "offPeakTimeList",
    "activeWeek",
    "peakTimeListNonWorkDay",
    "midPeakTimeListNonWorkDay",
    "offPeakTimeListNonWorkDay",
    "activeWeekNonWorkDay",
    "dayLightPeakTimeList",
    "dayLightMidPeakTimeList",
    "dayLightOffPeakTimeList",
    "dayLightActiveWeek",
    "dayLightPeakTimeListNonWorkDay",
    "dayLightMidPeakTimeListNonWorkDay",
    "dayLightOffPeakTimeListNonWorkDay",
    "dayLightActiveWeekNonWorkDay",
})


class TestBuildSlot:
    @pytest.mark.parametrize(
        ("start", "end", "price", "expected"),
        [
            ("00:30", "04:30", 0.05, "00:30_04:30_0.05"),
            ("16:00", "19:00", 1.0,  "16:00_19:00_1.00"),
            ("04:30", "16:00", 0.25, "04:30_16:00_0.25"),
            ("00:00", "23:30", 0.005, "00:00_23:30_0.01"),  # 2dp rounding
        ],
    )
    def test_format(self, start, end, price, expected):
        assert build_slot(start, end, price) == expected


class TestBuildSwitchModePayload:
    def test_minimal_call_contains_every_required_key(self):
        # The Phase 3.2 wire-level regression-net: cube 500s on any missing.
        payload = build_switch_mode_payload(dev_id="5613", work_status="1")
        assert REQUIRED_KEYS.issubset(payload)

    def test_weatherwatch_and_onlysave_forced_off(self):
        # weatherWatch conflicts with Predbat's economic plan; onlySave is
        # persisted state on the cube — leaving it set would be a footgun.
        payload = build_switch_mode_payload(dev_id="5613", work_status="1")
        assert payload["weatherWatch"] == "0"
        assert payload["onlySave"] == "0"

    def test_dev_id_coerced_to_string(self):
        payload = build_switch_mode_payload(dev_id=5613, work_status="1")
        assert payload["devId"] == "5613"
        assert isinstance(payload["devId"], str)

    def test_day_arrays_are_list_of_str(self):
        # int day arrays come back as 403 "token expired" — type matters.
        payload = build_switch_mode_payload(
            dev_id="5613",
            work_status="1",
            weekday_days=[1, 2, 3, 4, 5],
            weekend_days=[6, 7],
        )
        assert payload["activeWeek"] == ["1", "2", "3", "4", "5"]
        assert payload["activeWeekNonWorkDay"] == ["6", "7"]
        assert all(isinstance(d, str) for d in payload["activeWeek"])
        assert all(isinstance(d, str) for d in payload["activeWeekNonWorkDay"])

    def test_day_arrays_default_to_canonical_split(self):
        # Mon–Fri weekdays, Sat–Sun weekends. Cube uses 1-indexed ISO weekdays
        # (1=Mon … 7=Sun), NOT Python's 0-indexed weekday().
        payload = build_switch_mode_payload(dev_id="5613", work_status="1")
        assert payload["activeWeek"] == ["1", "2", "3", "4", "5"]
        assert payload["activeWeekNonWorkDay"] == ["6", "7"]

    def test_dst_day_arrays_default_to_non_dst_when_unset(self):
        # If the caller passes weekday_days but no dst_weekday_days, the DST
        # variant should mirror the non-DST one — keeps the cube happy when
        # the user has a single year-round profile.
        payload = build_switch_mode_payload(
            dev_id="5613", work_status="1", weekday_days=[1, 2, 3, 4],
        )
        assert payload["dayLightActiveWeek"] == ["1", "2", "3", "4"]

    def test_tou_type_native_int(self):
        # Cube rejects str touType with the misleading 403.
        payload = build_switch_mode_payload(dev_id="5613", work_status="2", tou_type=1)
        assert payload["touType"] == 1
        assert isinstance(payload["touType"], int)

    def test_dst_active_native_bool(self):
        # Cube rejects str/int dayLightSavingTime.
        payload = build_switch_mode_payload(
            dev_id="5613", work_status="1", dst_active=True
        )
        assert payload["dayLightSavingTime"] is True
        assert isinstance(payload["dayLightSavingTime"], bool)

    def test_reserve_socs_as_str(self):
        payload = build_switch_mode_payload(
            dev_id="5613",
            work_status="3",
            self_consumption_reserve_soc=15,
            backup_reserve_soc=80,
        )
        assert payload["selfConsumptioinReserveSoc"] == "15"
        assert payload["backupPowerReserveSoc"] == "80"

    def test_allow_grid_charge_as_bit_string(self):
        on = build_switch_mode_payload(
            dev_id="5613", work_status="1", allow_grid_charge=True
        )
        off = build_switch_mode_payload(
            dev_id="5613", work_status="1", allow_grid_charge=False
        )
        assert on["allowChargingXiaGrid"] == "1"
        assert off["allowChargingXiaGrid"] == "0"

    def test_slot_lists_passthrough(self):
        peak = ["16:00_19:00_1.00"]
        mid = ["04:30_16:00_0.20"]
        off = ["00:30_04:30_0.01"]
        payload = build_switch_mode_payload(
            dev_id="5613",
            work_status="2",
            peak_slots=peak,
            mid_peak_slots=mid,
            off_peak_slots=off,
        )
        assert payload["peakTimeList"] == peak
        assert payload["midPeakTimeList"] == mid
        assert payload["offPeakTimeList"] == off

    def test_none_slot_lists_become_empty(self):
        payload = build_switch_mode_payload(dev_id="5613", work_status="1")
        for key in (
            "peakTimeList", "midPeakTimeList", "offPeakTimeList",
            "peakTimeListNonWorkDay", "midPeakTimeListNonWorkDay",
            "offPeakTimeListNonWorkDay",
        ):
            assert payload[key] == []


class TestPayloadFromSwitchModeRead:
    """Mirror-from-readback path — used by the shim for revert + minimum-diff."""

    def test_mirrors_baseline(self, get_switch_mode):
        payload = payload_from_switch_mode_read("5613", get_switch_mode)
        # Mode + reserves carry through.
        assert payload["workStatus"] == "1"
        assert payload["selfConsumptioinReserveSoc"] == "20"
        assert payload["backupPowerReserveSoc"] == "100"
        # Slots carry through unchanged.
        assert payload["offPeakTimeList"] == ["00:30_04:30_0.05"]
        assert payload["midPeakTimeList"] == ["04:30_16:00_0.25"]
        assert payload["peakTimeList"] == ["16:00_19:00_0.40"]

    def test_overrides_apply(self, get_switch_mode):
        payload = payload_from_switch_mode_read(
            "5613",
            get_switch_mode,
            overrides={"workStatus": "2", "peakTimeList": ["20:00_21:00_1.00"]},
        )
        assert payload["workStatus"] == "2"
        assert payload["peakTimeList"] == ["20:00_21:00_1.00"]
        # Other fields untouched by the override.
        assert payload["offPeakTimeList"] == ["00:30_04:30_0.05"]

    def test_day_arrays_coerced_to_str_even_when_input_is_int(self, get_switch_mode):
        # Cube returns activeWeek as list[int] on read, but write requires list[str].
        assert all(isinstance(d, int) for d in get_switch_mode["activeWeek"])
        payload = payload_from_switch_mode_read("5613", get_switch_mode)
        assert payload["activeWeek"] == ["1", "2", "3", "4", "5"]
        assert all(isinstance(d, str) for d in payload["activeWeek"])

    def test_dst_arrays_fall_back_to_non_dst(self, get_switch_mode):
        # Most users don't set DST schedules — payload builder should mirror
        # the non-DST day mask so the write doesn't blank them.
        snapshot = dict(get_switch_mode)
        snapshot["dayLightActiveWeek"] = []
        snapshot["dayLightActiveWeekNonWorkDay"] = []
        payload = payload_from_switch_mode_read("5613", snapshot)
        assert payload["dayLightActiveWeek"] == ["1", "2", "3", "4", "5"]
        assert payload["dayLightActiveWeekNonWorkDay"] == ["6", "7"]

    def test_missing_reserves_default_safely(self):
        # Defensive: if the cube ever omits a reserve key, default to a value
        # that won't strand the battery.
        payload = payload_from_switch_mode_read("5613", {"workStatus": "1"})
        assert payload["selfConsumptioinReserveSoc"] == "20"
        assert payload["backupPowerReserveSoc"] == "100"

    def test_required_keys_still_present_via_mirror(self, get_switch_mode):
        payload = payload_from_switch_mode_read("5613", get_switch_mode)
        assert REQUIRED_KEYS.issubset(payload)
