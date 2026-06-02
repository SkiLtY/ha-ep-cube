"""Tests for the user-facing ep_cube.set_tou_schedule service.

The service is the wire-side companion to the bundled Lovelace editor card
(Phase 4.1). It accepts user-friendly "HH:MM-HH:MM" slot strings, validates
format + within-tier + cross-tier overlap, then writes the cube's full
switchMode payload preserving DST tier lists, day masks, reserves, and
per-tier prices from the cube's current state.

These tests exercise:
  - Pure validation helpers (good input passes, malformed/overlapping
    rejected with HomeAssistantError)
  - Service dispatch end-to-end against a fake client (one switchMode write
    per call, payload shape matches what the cube expects)
  - Shim coexistence: abandon_override is called when a Predbat override
    is in flight (user-wins)
  - switch_to_tou=True flips workStatus; default leaves it alone
  - Per-tier price preservation from the live cube state
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.ep_cube.const import (
    DEFAULT_TIER_PRICE_MID_PEAK,
    DEFAULT_TIER_PRICE_OFF_PEAK,
    DEFAULT_TIER_PRICE_PEAK,
    DOMAIN,
    SHIM_PRICE_OFF_PEAK,
    SHIM_PRICE_PEAK,
    WORK_STATUS_SELF_CONSUMPTION,
    WORK_STATUS_TOU,
)
from custom_components.ep_cube.services import (
    PredbatShim,
    SERVICE_SET_TOU_SCHEDULE,
    _existing_house_price,
    _first_non_shim_price,
    _parse_user_slot,
    _validate_day_profile,
    async_register_services,
    parse_tou_prices,
    parse_tou_schedule,
)


# ----------------------------------------------------------------------
# Pure validation helpers
# ----------------------------------------------------------------------
class TestParseUserSlot:
    @pytest.mark.parametrize("slot,expected", [
        ("00:00-23:59", (0, 23 * 60 + 59)),
        ("04:30-16:00", (4 * 60 + 30, 16 * 60)),
        ("16:00-19:00", (16 * 60, 19 * 60)),
    ])
    def test_valid_slots(self, slot, expected):
        assert _parse_user_slot(slot) == expected

    @pytest.mark.parametrize("slot", [
        "bad",
        "4:30-16:00",       # missing leading zero
        "24:00-25:00",      # hour > 23
        "12:60-13:00",      # minute > 59
        "12:30_13:00",      # wrong separator (wire format)
        "12:30-12:30",      # zero-length
        "20:00-04:00",      # midnight-crossing rejected (cube unsupported)
    ])
    def test_invalid_slots_raise(self, slot):
        with pytest.raises(ValueError):
            _parse_user_slot(slot)


class TestValidateDayProfile:
    def test_well_formed_profile_passes(self):
        _validate_day_profile(
            "workday",
            {
                "peak_workday": ["16:00-19:00"],
                "mid_peak_workday": ["04:30-16:00", "19:00-23:30"],
                "off_peak_workday": ["00:30-04:30"],
            },
        )

    def test_within_tier_overlap_rejected(self):
        with pytest.raises(HomeAssistantError, match="overlapping slots"):
            _validate_day_profile(
                "workday",
                {
                    "peak_workday": ["16:00-19:00", "18:00-20:00"],
                    "mid_peak_workday": [],
                    "off_peak_workday": [],
                },
            )

    def test_cross_tier_overlap_rejected(self):
        with pytest.raises(HomeAssistantError, match="overlaps"):
            _validate_day_profile(
                "workday",
                {
                    "peak_workday": ["16:00-19:00"],
                    "mid_peak_workday": ["18:00-20:00"],  # overlaps peak 18:00-19:00
                    "off_peak_workday": [],
                },
            )

    def test_exact_cross_tier_duplicate_rejected(self):
        with pytest.raises(HomeAssistantError, match="overlaps"):
            _validate_day_profile(
                "workday",
                {
                    "peak_workday": ["16:00-19:00"],
                    "mid_peak_workday": ["16:00-19:00"],
                    "off_peak_workday": [],
                },
            )

    def test_adjacent_slots_not_an_overlap(self):
        # 04:30-16:00 ends exactly where 16:00-19:00 begins — that's fine.
        _validate_day_profile(
            "workday",
            {
                "peak_workday": ["16:00-19:00"],
                "mid_peak_workday": ["04:30-16:00"],
                "off_peak_workday": [],
            },
        )

    def test_malformed_slot_surfaces_field_label(self):
        with pytest.raises(HomeAssistantError, match="workday mid_peak_workday"):
            _validate_day_profile(
                "workday",
                {
                    "peak_workday": [],
                    "mid_peak_workday": ["nope"],
                    "off_peak_workday": [],
                },
            )

    def test_all_empty_profile_passes(self):
        # Clearing all tiers is a valid operation (means "no TOU slots configured").
        _validate_day_profile(
            "workday",
            {
                "peak_workday": [],
                "mid_peak_workday": [],
                "off_peak_workday": [],
            },
        )


class TestExistingHousePrice:
    def test_preserves_existing_non_shim_price(self):
        # Cube has a real user slot at 0.40 — set_tou_schedule should
        # reuse that price for new slots in the same tier.
        assert _existing_house_price(["16:00_19:00_0.40"], default=0.99) == 0.40

    def test_falls_back_to_default_when_empty(self):
        assert _existing_house_price([], default=0.25) == 0.25
        assert _existing_house_price(None, default=0.25) == 0.25

    def test_skips_shim_signature_slots(self):
        # Stale shim slot at SHIM_PRICE_PEAK shouldn't poison the price.
        assert _existing_house_price(
            [f"00:00_06:00_{SHIM_PRICE_PEAK:.2f}", "16:00_19:00_0.40"],
            default=0.99,
        ) == 0.40

    def test_falls_back_when_only_shim_slots(self):
        # All slots are shim signatures (stale from a Predbat run) → default wins.
        assert _existing_house_price(
            [f"00:00_06:00_{SHIM_PRICE_PEAK:.2f}",
             f"06:00_07:00_{SHIM_PRICE_OFF_PEAK:.2f}"],
            default=0.42,
        ) == 0.42

    def test_malformed_slot_tolerated(self):
        # Garbage slot is skipped; next valid one wins.
        assert _existing_house_price(["garbage", "16:00_19:00_0.40"], default=0.99) == 0.40


# ----------------------------------------------------------------------
# parse_tou_schedule — used by the operating-mode select to expose the
# cube's current schedule as an entity attribute, so the editor card can
# hydrate from real state (Phase 4.2 / v0.6.2).
# ----------------------------------------------------------------------
class TestParseTouSchedule:
    def test_empty_state_returns_six_empty_lists(self):
        result = parse_tou_schedule({})
        assert result == {
            "workday": {"peak": [], "mid_peak": [], "off_peak": []},
            "weekend": {"peak": [], "mid_peak": [], "off_peak": []},
        }

    def test_wire_slots_converted_to_user_format(self):
        state = {
            "peakTimeList": ["16:00_19:00_0.40"],
            "midPeakTimeList": ["04:30_16:00_0.25", "19:00_23:59_0.25"],
            "offPeakTimeList": ["00:30_04:30_0.05"],
        }
        result = parse_tou_schedule(state)
        assert result["workday"]["peak"] == ["16:00-19:00"]
        assert result["workday"]["mid_peak"] == ["04:30-16:00", "19:00-23:59"]
        assert result["workday"]["off_peak"] == ["00:30-04:30"]

    def test_weekend_lists_parsed_separately(self):
        state = {
            "peakTimeList": ["16:00_19:00_0.40"],
            "peakTimeListNonWorkDay": ["17:00_20:00_0.40"],
            "offPeakTimeListNonWorkDay": ["02:00_05:00_0.05"],
        }
        result = parse_tou_schedule(state)
        assert result["workday"]["peak"] == ["16:00-19:00"]
        assert result["weekend"]["peak"] == ["17:00-20:00"]
        assert result["weekend"]["off_peak"] == ["02:00-05:00"]

    def test_shim_signature_slots_stripped(self):
        # A stale shim charge slot (SHIM_PRICE_OFF_PEAK = 0.01) shouldn't
        # leak into the user-facing schedule.
        state = {
            "offPeakTimeList": [
                f"08:00_09:00_{SHIM_PRICE_OFF_PEAK:.2f}",  # shim slot
                "00:30_04:30_0.05",                         # real user slot
            ],
            "peakTimeList": [f"00:00_06:00_{SHIM_PRICE_PEAK:.2f}"],  # shim only
        }
        result = parse_tou_schedule(state)
        assert result["workday"]["off_peak"] == ["00:30-04:30"]
        assert result["workday"]["peak"] == []

    def test_malformed_slot_dropped_silently(self):
        state = {
            "peakTimeList": ["16:00_19:00_0.40", "garbage", ""],
        }
        result = parse_tou_schedule(state)
        # Garbage and empty entries are filtered; valid slot survives.
        assert result["workday"]["peak"] == ["16:00-19:00"]

    def test_does_not_surface_dst_lists(self):
        # DST tier lists are preserved server-side but not exposed via the
        # card (out of scope for the editor MVP). Confirm they don't leak
        # into the parsed shape.
        state = {
            "peakTimeList": ["16:00_19:00_0.40"],
            "dayLightPeakTimeList": ["17:00_20:00_0.40"],
        }
        result = parse_tou_schedule(state)
        assert result["workday"]["peak"] == ["16:00-19:00"]
        # No "dst" key, no leakage into workday/weekend.
        assert set(result.keys()) == {"workday", "weekend"}

    def test_strips_current_shim_prices_only(self):
        # v0.7 dropped the v0.6.3 legacy migration window — only current
        # synthetic prices (2.22 / 3.33 / 4.44) are recognised. Legacy
        # values (0.01 / 0.20 / 1.00) would now look like genuine user
        # slots. By v0.7's ship date any in-flight pre-v0.6.3 overrides
        # have long since rotated out, so the strip-set narrowing is safe.
        state = {
            "peakTimeList": [
                f"00:00_06:00_{SHIM_PRICE_PEAK:.2f}",    # new (4.44) — stripped
                "16:00_19:00_0.40",                       # genuine user slot — kept
            ],
            "offPeakTimeList": [
                f"08:00_09:00_{SHIM_PRICE_OFF_PEAK:.2f}", # new (2.22) — stripped
                "00:30_04:30_0.05",                       # genuine user slot — kept
            ],
        }
        result = parse_tou_schedule(state)
        assert result["workday"]["peak"] == ["16:00-19:00"]
        assert result["workday"]["off_peak"] == ["00:30-04:30"]

    def test_legacy_shim_prices_no_longer_stripped(self):
        # Confirm the v0.7 narrowing: 0.01 / 0.20 / 1.00 would now leak
        # through as user slots. This is intentional — they collide with
        # realistic fixed-tariff prices and the legacy migration window
        # has expired. A user with a 1.00 p/kWh peak slot will now see it.
        state = {
            "peakTimeList": ["06:00_07:00_1.00"],
            "offPeakTimeList": ["09:00_10:00_0.01"],
        }
        result = parse_tou_schedule(state)
        assert result["workday"]["peak"] == ["06:00-07:00"]
        assert result["workday"]["off_peak"] == ["09:00-10:00"]


# ----------------------------------------------------------------------
# Service dispatch end-to-end (via hass.services.async_call)
# ----------------------------------------------------------------------
@pytest.fixture
async def service_ready(hass, fake_client):
    """Register services + populate hass.data with a shim + client."""
    shim = PredbatShim(hass=hass, entry_id="test-entry", client=fake_client)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["test-entry"] = {
        "shim": shim,
        "client": fake_client,
        "coordinator": None,
    }
    await async_register_services(hass)
    yield shim, fake_client
    # Clean up any timers the shim might have armed in a test path.
    shim.cancel_revert_timer()
    # Per-test service cleanup so the next test re-registers fresh.
    for service in (
        "set_tou_schedule", "charge_start", "charge_stop",
        "discharge_start", "discharge_stop",
        "charge_freeze", "discharge_freeze",
        "idle", "debug_freeze",
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    hass.data[DOMAIN].pop("test-entry", None)


def _good_payload(**overrides):
    """Minimal valid call payload — clears every tier."""
    base = {
        "peak_workday": [],
        "mid_peak_workday": [],
        "off_peak_workday": [],
        "peak_weekend": [],
        "mid_peak_weekend": [],
        "off_peak_weekend": [],
    }
    base.update(overrides)
    return base


class TestServiceDispatch:
    async def test_writes_two_writes_when_cube_in_non_tou_mode(self, hass, service_ready):
        # Fixture has workStatus="1" (self-consumption). v0.6.4: schedule
        # writes need a 2-write dance when cube isn't in TOU, because the
        # cube silently drops tier-list updates while in non-TOU mode.
        # Write A flips to TOU + applies tier lists; write B flips back.
        shim, client = service_ready
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["16:00-19:00"]),
            blocking=True,
        )
        assert client.switch_mode.await_count == 2
        # Cube state was read first (so we can preserve DST + prices).
        assert client.get_switch_mode.await_count == 1
        # Write A: workStatus=TOU.
        write_a = client.switch_mode.await_args_list[0].args[0]
        assert write_a["workStatus"] == WORK_STATUS_TOU
        assert write_a["peakTimeList"] == ["16:00_19:00_0.40"]
        # Write B: workStatus back to self_consumption (the original).
        write_b = client.switch_mode.await_args_list[1].args[0]
        assert write_b["workStatus"] == WORK_STATUS_SELF_CONSUMPTION
        # B still includes the tier lists (cube ignores them on non-TOU
        # transition, but we send them defensively).
        assert write_b["peakTimeList"] == ["16:00_19:00_0.40"]

    async def test_preserves_existing_tier_price(self, hass, service_ready, get_switch_mode):
        # get_switch_mode fixture has peakTimeList=["16:00_19:00_0.40"] — that
        # 0.40 should be propagated to the new slot we write.
        shim, client = service_ready
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["17:00-20:00"]),
            blocking=True,
        )
        body = client.switch_mode.await_args.args[0]
        assert body["peakTimeList"] == ["17:00_20:00_0.40"]

    async def test_uses_default_price_when_tier_empty(self, hass, service_ready, fake_client):
        # Empty peakTimeListNonWorkDay in the fixture → default peak price fires.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_weekend=["18:00-21:00"]),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["peakTimeListNonWorkDay"] == [
            f"18:00_21:00_{DEFAULT_TIER_PRICE_PEAK:.2f}"
        ]

    async def test_default_does_not_flip_work_status(self, hass, service_ready, get_switch_mode):
        shim, client = service_ready
        # get_switch_mode fixture has workStatus="1" (self-consumption).
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_TOU_SCHEDULE, _good_payload(), blocking=True
        )
        body = client.switch_mode.await_args.args[0]
        assert body["workStatus"] == WORK_STATUS_SELF_CONSUMPTION

    async def test_switch_to_tou_flag_flips_work_status(self, hass, service_ready):
        shim, client = service_ready
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(switch_to_tou=True),
            blocking=True,
        )
        body = client.switch_mode.await_args.args[0]
        assert body["workStatus"] == WORK_STATUS_TOU

    async def test_single_write_when_switch_to_tou_true(self, hass, service_ready):
        # switch_to_tou=True means user wants the cube to end up in TOU mode,
        # so no 2-write dance — single write applies both mode + tier lists.
        shim, client = service_ready
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["16:00-19:00"], switch_to_tou=True),
            blocking=True,
        )
        assert client.switch_mode.await_count == 1

    async def test_single_write_when_cube_already_in_tou(self, hass, service_ready, fake_client):
        # If the cube is already in TOU mode, a TOU → TOU write applies
        # tier lists cleanly. No 2-write dance needed.
        fake_client.get_switch_mode.return_value = {
            "devId": "5613", "workStatus": "2",   # already TOU
            "selfConsumptioinReserveSoc": "20", "backupPowerReserveSoc": "100",
            "allowChargingXiaGrid": "1",
            "peakTimeList": ["16:00_19:00_0.40"], "midPeakTimeList": [], "offPeakTimeList": [],
            "peakTimeListNonWorkDay": [], "midPeakTimeListNonWorkDay": [],
            "offPeakTimeListNonWorkDay": [],
            "activeWeek": [1, 2, 3, 4, 5], "activeWeekNonWorkDay": [6, 7],
            "dayLightSavingTime": False,
            "dayLightPeakTimeList": [], "dayLightMidPeakTimeList": [], "dayLightOffPeakTimeList": [],
            "dayLightPeakTimeListNonWorkDay": [],
            "dayLightMidPeakTimeListNonWorkDay": [],
            "dayLightOffPeakTimeListNonWorkDay": [],
            "dayLightActiveWeek": [1, 2, 3, 4, 5],
            "dayLightActiveWeekNonWorkDay": [6, 7],
            "touType": 0,
        }
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["17:00-20:00"]),
            blocking=True,
        )
        assert fake_client.switch_mode.await_count == 1
        body = fake_client.switch_mode.await_args.args[0]
        assert body["workStatus"] == WORK_STATUS_TOU
        assert body["peakTimeList"] == ["17:00_20:00_0.40"]

    async def test_two_write_clears_schedule_from_non_tou(self, hass, service_ready):
        # Direct reproduction of the v0.6.4 bug user surfaced 2026-05-29: in
        # self-consumption mode, save an empty schedule. Pre-v0.6.4 the cube
        # silently dropped the tier-list write. With 2-write fix, the empty
        # tier lists land via write A (TOU mode), then write B restores
        # self-consumption — final state is empty schedule + correct mode.
        shim, client = service_ready
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_TOU_SCHEDULE, _good_payload(), blocking=True
        )
        assert client.switch_mode.await_count == 2
        write_a = client.switch_mode.await_args_list[0].args[0]
        write_b = client.switch_mode.await_args_list[1].args[0]
        # Write A: empties tier lists while in TOU mode (where cube honours them).
        assert write_a["workStatus"] == WORK_STATUS_TOU
        assert write_a["peakTimeList"] == []
        assert write_a["midPeakTimeList"] == []
        assert write_a["offPeakTimeList"] == []
        # Write B: back to self-consumption.
        assert write_b["workStatus"] == WORK_STATUS_SELF_CONSUMPTION

    async def test_preserves_dst_tier_lists_from_live_state(self, hass, service_ready, fake_client):
        # Inject a live state with non-empty DST tier lists. Our write must
        # round-trip them untouched even though set_tou_schedule's API only
        # exposes non-DST tiers.
        fake_client.get_switch_mode.return_value = {
            "devId": "5613", "workStatus": "1",
            "selfConsumptioinReserveSoc": "20", "backupPowerReserveSoc": "100",
            "allowChargingXiaGrid": "1", "weatherWatch": "0", "onlySave": "0",
            "peakTimeList": [], "midPeakTimeList": [], "offPeakTimeList": [],
            "peakTimeListNonWorkDay": [], "midPeakTimeListNonWorkDay": [],
            "offPeakTimeListNonWorkDay": [],
            "activeWeek": [1, 2, 3, 4, 5], "activeWeekNonWorkDay": [6, 7],
            "dayLightSavingTime": False,
            "dayLightPeakTimeList": ["17:00_20:00_0.45"],   # DST data we must preserve
            "dayLightMidPeakTimeList": ["05:30_17:00_0.30"],
            "dayLightOffPeakTimeList": ["00:00_05:30_0.10"],
            "dayLightPeakTimeListNonWorkDay": [],
            "dayLightMidPeakTimeListNonWorkDay": [],
            "dayLightOffPeakTimeListNonWorkDay": [],
            "dayLightActiveWeek": [1, 2, 3, 4, 5],
            "dayLightActiveWeekNonWorkDay": [6, 7],
            "touType": 0,
        }
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["16:00-19:00"]),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["dayLightPeakTimeList"] == ["17:00_20:00_0.45"]
        assert body["dayLightMidPeakTimeList"] == ["05:30_17:00_0.30"]
        assert body["dayLightOffPeakTimeList"] == ["00:00_05:30_0.10"]

    async def test_strips_stale_shim_slots_from_live_state(self, hass, service_ready, fake_client):
        # If a prior Predbat run left a shim-signature slot on the cube
        # (price = SHIM_PRICE_PEAK = 1.00), our write must not re-send it
        # — the cube would then carry it indefinitely. The shim's existing
        # _strip_shim_slots runs on snapshot; we reuse it here too.
        fake_client.get_switch_mode.return_value = {
            "devId": "5613", "workStatus": "2",
            "selfConsumptioinReserveSoc": "20", "backupPowerReserveSoc": "100",
            "allowChargingXiaGrid": "1", "weatherWatch": "0", "onlySave": "0",
            "peakTimeList": [
                "16:00_19:00_0.40",                       # user's real slot — keep
                f"20:00_21:00_{SHIM_PRICE_PEAK:.2f}",     # shim leftover — strip
            ],
            "midPeakTimeList": [], "offPeakTimeList": [],
            "peakTimeListNonWorkDay": [], "midPeakTimeListNonWorkDay": [],
            "offPeakTimeListNonWorkDay": [],
            "activeWeek": [1, 2, 3, 4, 5], "activeWeekNonWorkDay": [6, 7],
            "dayLightSavingTime": False,
            "dayLightPeakTimeList": [], "dayLightMidPeakTimeList": [],
            "dayLightOffPeakTimeList": [],
            "dayLightPeakTimeListNonWorkDay": [],
            "dayLightMidPeakTimeListNonWorkDay": [],
            "dayLightOffPeakTimeListNonWorkDay": [],
            "dayLightActiveWeek": [1, 2, 3, 4, 5],
            "dayLightActiveWeekNonWorkDay": [6, 7],
            "touType": 0,
        }
        # User overwrites peakTimeList entirely — no stale slot can survive.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["17:00-19:00"]),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        # Our written value preserves the user's 0.40 (first non-shim price found).
        assert body["peakTimeList"] == ["17:00_19:00_0.40"]


class TestShimCoexistence:
    async def test_abandons_active_shim_override(self, hass, service_ready):
        shim, client = service_ready

        # Simulate an in-flight Predbat override.
        from datetime import timedelta
        from homeassistant.util import dt as dt_util
        await shim.charge_start(
            end_time=dt_util.utcnow() + timedelta(minutes=30),
            target_soc_pct=90.0,
        )
        assert shim.is_active is True
        assert shim._baseline is not None

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["16:00-19:00"]),
            blocking=True,
        )
        # Override cleared, baseline dropped, no revert was triggered.
        assert shim.is_active is False
        assert shim._baseline is None

    async def test_no_op_when_no_shim_override(self, hass, service_ready):
        shim, client = service_ready
        assert shim.is_active is False
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["16:00-19:00"]),
            blocking=True,
        )
        # 2-write dance (cube in non-TOU per fixture); no spurious shim revert
        # on top — shim is not active so it has no baseline to restore.
        assert client.switch_mode.await_count == 2


class TestServiceValidationErrors:
    async def test_malformed_slot_raises_before_cloud_io(self, hass, service_ready):
        shim, client = service_ready
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_TOU_SCHEDULE,
                _good_payload(peak_workday=["bad"]),
                blocking=True,
            )
        client.switch_mode.assert_not_awaited()
        client.get_switch_mode.assert_not_awaited()

    async def test_within_tier_overlap_raises_before_cloud_io(self, hass, service_ready):
        shim, client = service_ready
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_TOU_SCHEDULE,
                _good_payload(peak_workday=["16:00-19:00", "18:00-20:00"]),
                blocking=True,
            )
        client.switch_mode.assert_not_awaited()


# ----------------------------------------------------------------------
# Phase 4.1++ (v0.7) — per-tier rate entry on TOU card + service
# ----------------------------------------------------------------------
class TestFirstNonShimPrice:
    """Helper extracted from _existing_house_price so parse_tou_prices can
    share the slot-scanning logic. None when no real (non-shim) slot exists."""

    def test_returns_first_real_price(self):
        assert _first_non_shim_price(["16:00_19:00_0.40"]) == 0.40

    def test_returns_none_for_empty(self):
        assert _first_non_shim_price([]) is None
        assert _first_non_shim_price(None) is None

    def test_skips_shim_slots(self):
        assert _first_non_shim_price(
            [f"00:00_06:00_{SHIM_PRICE_PEAK:.2f}", "16:00_19:00_0.40"]
        ) == 0.40

    def test_returns_none_when_only_shim(self):
        # All shim — None signals "no real price to display" so the card
        # falls back to the placeholder rather than showing 2.22 / 3.33 / 4.44.
        assert _first_non_shim_price(
            [f"00:00_06:00_{SHIM_PRICE_PEAK:.2f}",
             f"06:00_07:00_{SHIM_PRICE_OFF_PEAK:.2f}"]
        ) is None

    def test_malformed_slot_tolerated(self):
        assert _first_non_shim_price(["garbage", "16:00_19:00_0.40"]) == 0.40

    def test_existing_house_price_still_falls_back(self):
        # _existing_house_price is a thin wrapper over _first_non_shim_price
        # with a default fallback — regression check that the refactor
        # didn't change its behaviour.
        assert _existing_house_price([], default=0.25) == 0.25
        assert _existing_house_price(["16:00_19:00_0.40"], default=0.99) == 0.40


class TestParseTouPrices:
    """parse_tou_prices powers the operating-mode select's tou_prices
    attribute, which the card hydrates from on bind + after save."""

    def test_empty_state_returns_six_nulls(self):
        result = parse_tou_prices({})
        assert result == {
            "workday": {"peak": None, "mid_peak": None, "off_peak": None},
            "weekend": {"peak": None, "mid_peak": None, "off_peak": None},
        }

    def test_populated_state_returns_first_price_per_tier(self):
        state = {
            "peakTimeList": ["16:00_19:00_0.40", "20:00_22:00_0.40"],
            "midPeakTimeList": ["04:30_16:00_0.25"],
            "offPeakTimeList": ["00:30_04:30_0.05"],
            "peakTimeListNonWorkDay": ["17:00_20:00_0.42"],
            "midPeakTimeListNonWorkDay": ["05:00_17:00_0.27"],
            "offPeakTimeListNonWorkDay": ["00:00_05:00_0.07"],
        }
        result = parse_tou_prices(state)
        assert result["workday"] == {"peak": 0.40, "mid_peak": 0.25, "off_peak": 0.05}
        assert result["weekend"] == {"peak": 0.42, "mid_peak": 0.27, "off_peak": 0.07}

    def test_empty_tier_returns_none_not_default(self):
        # Critical: if a tier has no slots, we return None so the card shows
        # a placeholder instead of misleadingly displaying DEFAULT_TIER_PRICE_*
        # as if it were a real price.
        state = {
            "peakTimeList": ["16:00_19:00_0.40"],
            "midPeakTimeList": [],
            "offPeakTimeList": [],
        }
        result = parse_tou_prices(state)
        assert result["workday"]["peak"] == 0.40
        assert result["workday"]["mid_peak"] is None
        assert result["workday"]["off_peak"] is None

    def test_shim_signature_slots_stripped(self):
        # A tier that contains ONLY shim slots (stale from a Predbat run)
        # should look empty — returning None so we don't surface 4.44 etc
        # as a "real" price the user would otherwise have to clear.
        state = {
            "peakTimeList": [f"00:00_06:00_{SHIM_PRICE_PEAK:.2f}"],
            "offPeakTimeList": [
                f"08:00_09:00_{SHIM_PRICE_OFF_PEAK:.2f}",
                "00:30_04:30_0.05",
            ],
        }
        result = parse_tou_prices(state)
        assert result["workday"]["peak"] is None
        assert result["workday"]["off_peak"] == 0.05

    def test_malformed_slot_tolerated(self):
        state = {"peakTimeList": ["garbage", "", "16:00_19:00_0.40"]}
        assert parse_tou_prices(state)["workday"]["peak"] == 0.40

    def test_does_not_surface_dst_prices(self):
        # DST tier lists are preserved server-side but the card doesn't
        # expose them. Confirm DST prices don't leak into workday/weekend.
        state = {
            "peakTimeList": ["16:00_19:00_0.40"],
            "dayLightPeakTimeList": ["17:00_20:00_0.99"],
        }
        result = parse_tou_prices(state)
        assert result["workday"]["peak"] == 0.40
        assert set(result.keys()) == {"workday", "weekend"}


class TestExplicitPrices:
    """Service-level: the optional `prices` arg overrides preserve-from-cube
    on a per-tier basis. Unspecified tiers keep existing behaviour."""

    async def test_full_prices_dict_overrides_cube(self, hass, service_ready, fake_client):
        # Cube has 0.40 peak — explicit 25 p/kWh should override.
        # Service interface is p/kWh; handler divides by 100 for wire format
        # (which is in £/kWh-flavoured units, 2dp). 25 p/kWh → 0.25 wire.
        # All 6 tiers given slots + explicit prices to confirm each one
        # routes through the conversion correctly.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(
                peak_workday=["16:00-19:00"],
                mid_peak_workday=["04:30-16:00"],
                off_peak_workday=["00:30-04:30"],
                peak_weekend=["18:00-21:00"],
                mid_peak_weekend=["05:00-18:00"],
                off_peak_weekend=["00:00-05:00"],
                prices={
                    "peak_workday": 25,
                    "mid_peak_workday": 15,
                    "off_peak_workday": 5,
                    "peak_weekend": 30,
                    "mid_peak_weekend": 12,
                    "off_peak_weekend": 4,
                },
            ),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["peakTimeList"] == ["16:00_19:00_0.25"]
        assert body["midPeakTimeList"] == ["04:30_16:00_0.15"]
        assert body["offPeakTimeList"] == ["00:30_04:30_0.05"]
        assert body["peakTimeListNonWorkDay"] == ["18:00_21:00_0.30"]
        assert body["midPeakTimeListNonWorkDay"] == ["05:00_18:00_0.12"]
        assert body["offPeakTimeListNonWorkDay"] == ["00:00_05:00_0.04"]

    async def test_partial_prices_preserves_unspecified(self, hass, service_ready, fake_client):
        # Only peak_workday explicit (25 p/kWh → 0.25 wire). Other tiers
        # fall back to cube's existing price (mid 0.25, off 0.05 per the
        # get_switch_mode fixture) or DEFAULT_TIER_PRICE_* when the cube's
        # tier is empty.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(
                peak_workday=["16:00-19:00"],
                mid_peak_workday=["04:30-16:00"],
                off_peak_workday=["00:30-04:30"],
                prices={"peak_workday": 25},
            ),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["peakTimeList"] == ["16:00_19:00_0.25"]
        # mid_peak preserves cube's 0.25 wire (= 25 p/kWh); off_peak preserves 0.05.
        assert body["midPeakTimeList"] == ["04:30_16:00_0.25"]
        assert body["offPeakTimeList"] == ["00:30_04:30_0.05"]

    async def test_explicit_price_for_empty_cube_tier(self, hass, service_ready, fake_client):
        # Cube has empty peakTimeListNonWorkDay. Without `prices`, default
        # DEFAULT_TIER_PRICE_PEAK (0.40 wire = 40 p/kWh) fires. With `prices`,
        # the explicit value wins (35 p/kWh → 0.35 wire).
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(
                peak_weekend=["18:00-21:00"],
                prices={"peak_weekend": 35},
            ),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["peakTimeListNonWorkDay"] == ["18:00_21:00_0.35"]

    async def test_sub_penny_price_rounds_to_1p(self, hass, service_ready, fake_client):
        # Cube wire format is 2dp on £-scale = 1p precision. The Octopus
        # Agile API quotes to 0.01p resolution, so 19.25 p/kWh comes through
        # as 0.1925 wire which rounds to 0.19 = 19p. Documented in services.py
        # comment + card hint text.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(
                off_peak_workday=["00:00-04:00"],
                prices={"off_peak_workday": 19.25},
            ),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["offPeakTimeList"] == ["00:00_04:00_0.19"]

    async def test_no_prices_arg_preserves_cube(self, hass, service_ready, fake_client):
        # Regression: omitting `prices` entirely keeps the pre-v0.7 behaviour
        # (preserve from cube → DEFAULT_TIER_PRICE_* fallback).
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["17:00-20:00"]),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["peakTimeList"] == ["17:00_20:00_0.40"]

    async def test_empty_prices_dict_treated_as_omitted(self, hass, service_ready, fake_client):
        # The card sends `prices` only when it has values. Passing an empty
        # dict from a Developer Tools call shouldn't break — falls through
        # to preserve-from-cube.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(peak_workday=["17:00-20:00"], prices={}),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["peakTimeList"] == ["17:00_20:00_0.40"]

    @pytest.mark.parametrize("bad_price", [-1.0, 1000.0, 99999.0])
    async def test_out_of_range_price_rejected(self, hass, service_ready, bad_price):
        shim, client = service_ready
        with pytest.raises(Exception):  # voluptuous Invalid wrapped to HA error
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_TOU_SCHEDULE,
                _good_payload(
                    peak_workday=["16:00-19:00"],
                    prices={"peak_workday": bad_price},
                ),
                blocking=True,
            )
        client.switch_mode.assert_not_awaited()

    async def test_unknown_price_key_rejected(self, hass, service_ready):
        # voluptuous schema only accepts the 6 documented keys. Typos like
        # "peakWorkday" or "off-peak_workday" fail closed.
        shim, client = service_ready
        with pytest.raises(Exception):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_TOU_SCHEDULE,
                _good_payload(
                    peak_workday=["16:00-19:00"],
                    prices={"peakWorkday": 9.99},
                ),
                blocking=True,
            )
        client.switch_mode.assert_not_awaited()

    async def test_string_price_coerced_to_float(self, hass, service_ready, fake_client):
        # Developer Tools sends YAML strings — voluptuous's vol.Coerce(float)
        # accepts "25" the same as 25. Confirms the schema is forgiving.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(
                peak_workday=["16:00-19:00"],
                prices={"peak_workday": "25"},
            ),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["peakTimeList"] == ["16:00_19:00_0.25"]

    async def test_explicit_prices_with_2write_dance(self, hass, service_ready, fake_client):
        # Confirm explicit prices land via write A (the TOU-mode write where
        # the cube actually accepts tier-list edits). Write B should also
        # carry the same prices defensively even though the cube drops the
        # tier-list portion of B. 12 p/kWh → 0.12 wire.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(
                peak_workday=["16:00-19:00"],
                prices={"peak_workday": 12},
            ),
            blocking=True,
        )
        assert fake_client.switch_mode.await_count == 2
        write_a = fake_client.switch_mode.await_args_list[0].args[0]
        write_b = fake_client.switch_mode.await_args_list[1].args[0]
        assert write_a["peakTimeList"] == ["16:00_19:00_0.12"]
        assert write_b["peakTimeList"] == ["16:00_19:00_0.12"]

    async def test_zero_price_accepted(self, hass, service_ready, fake_client):
        # 0.00 is valid — some fixed-tariff users have a free-period slot.
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_TOU_SCHEDULE,
            _good_payload(
                off_peak_workday=["00:00-04:00"],
                prices={"off_peak_workday": 0.0},
            ),
            blocking=True,
        )
        body = fake_client.switch_mode.await_args.args[0]
        assert body["offPeakTimeList"] == ["00:00_04:00_0.00"]
