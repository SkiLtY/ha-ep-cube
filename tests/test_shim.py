"""Tests for the Predbat shim (services.PredbatShim).

The shim is the load-bearing piece of the Predbat integration: it translates
Predbat's window-based commands into the cube's price-tier TOU model, with
strict idempotency (cloud-write budget ≤12/day target) and lazy baseline
snapshotting (one capture per shim lifetime).

These tests instantiate PredbatShim against a fake client (no HTTP) so we
can assert call patterns directly:
  - _matches_active short-circuits redundant calls
  - Baseline is captured once on first override and reused on revert
  - Auto-revert timer fires _revert_to_baseline
  - abandon_override clears state without touching the cube
  - patch_baseline updates mid-window without breaking the revert
  - _strip_shim_slots cleans stale synthetic-price slots from baseline
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.ep_cube.const import (
    SHIM_PRICE_MID_PEAK,
    SHIM_PRICE_OFF_PEAK,
    SHIM_PRICE_PEAK,
    WORK_STATUS_TOU,
)
from custom_components.ep_cube.services import (
    KIND_CHARGE,
    KIND_DISCHARGE,
    KIND_FREEZE,
    PredbatShim,
    _slot_is_shim_signature,
    _strip_shim_slots,
)


@pytest.fixture
def shim(hass, fake_client) -> PredbatShim:
    instance = PredbatShim(hass=hass, entry_id="test-entry", client=fake_client)
    yield instance
    # PHCC's hass fixture fails the test on any lingering timer; the shim
    # arms a one-shot revert via async_track_point_in_utc_time on every
    # override, so cancel it in teardown.
    instance.cancel_revert_timer()


def _future(minutes: int):
    return dt_util.utcnow() + timedelta(minutes=minutes)


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
class TestShimSignature:
    @pytest.mark.parametrize("slot", [
        "00:00_06:00_0.01",     # SHIM_PRICE_OFF_PEAK
        "12:00_18:00_0.20",     # SHIM_PRICE_MID_PEAK
        "18:00_21:00_1.00",     # SHIM_PRICE_PEAK
    ])
    def test_synthetic_prices_detected(self, slot):
        assert _slot_is_shim_signature(slot) is True

    @pytest.mark.parametrize("slot", [
        "00:30_04:30_0.05",     # user's real off-peak price
        "04:30_16:00_0.25",     # user's real mid-peak
        "16:00_19:00_0.40",     # user's real peak
        "garbage",
    ])
    def test_user_prices_not_detected(self, slot):
        assert _slot_is_shim_signature(slot) is False


class TestStripShimSlots:
    def test_strips_only_synthetic_prices(self):
        state = {
            "workStatus": "2",
            "peakTimeList": ["16:00_19:00_0.40", "20:00_21:00_1.00"],
            "midPeakTimeList": ["04:30_16:00_0.25"],
            "offPeakTimeList": ["00:30_04:30_0.01"],
        }
        cleaned, n = _strip_shim_slots(state)
        assert n == 2  # one peak + one off-peak match synthetic prices
        assert cleaned["peakTimeList"] == ["16:00_19:00_0.40"]
        assert cleaned["offPeakTimeList"] == []
        # User's mid-peak survives (0.25 ≠ 0.20).
        assert cleaned["midPeakTimeList"] == ["04:30_16:00_0.25"]

    def test_no_op_when_clean(self, get_switch_mode):
        cleaned, n = _strip_shim_slots(get_switch_mode)
        assert n == 0
        assert cleaned["peakTimeList"] == get_switch_mode["peakTimeList"]

    def test_input_not_mutated(self):
        state = {"peakTimeList": ["20:00_21:00_1.00"]}
        original = list(state["peakTimeList"])
        _strip_shim_slots(state)
        assert state["peakTimeList"] == original


# ----------------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------------
class TestIdempotency:
    async def test_charge_start_short_circuits_on_identical_params(self, shim, fake_client):
        end = _future(30)
        await shim.charge_start(end_time=end, target_soc_pct=90.0)
        assert fake_client.switch_mode.await_count == 1

        # Same end_time + target → no second write.
        await shim.charge_start(end_time=end, target_soc_pct=90.0)
        assert fake_client.switch_mode.await_count == 1

    async def test_charge_start_writes_again_on_different_end_time(self, shim, fake_client):
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        await shim.charge_start(end_time=_future(60), target_soc_pct=90.0)
        assert fake_client.switch_mode.await_count == 2

    async def test_charge_start_writes_again_on_different_target_soc(
        self, shim, fake_client
    ):
        end = _future(30)
        await shim.charge_start(end_time=end, target_soc_pct=80.0)
        await shim.charge_start(end_time=end, target_soc_pct=95.0)
        assert fake_client.switch_mode.await_count == 2


# ----------------------------------------------------------------------
# Baseline lifecycle
# ----------------------------------------------------------------------
class TestBaselineSnapshot:
    async def test_first_override_snapshots_baseline(self, shim, fake_client):
        assert shim._baseline is None
        await shim.charge_freeze(end_time=_future(30))
        assert shim._baseline is not None
        fake_client.get_switch_mode.assert_awaited_once()

    async def test_baseline_reused_across_overrides(self, shim, fake_client):
        await shim.charge_freeze(end_time=_future(30))
        await shim.discharge_start(end_time=_future(60), target_soc_pct=10.0)
        # get_switch_mode (snapshot) called once, switch_mode (override) called twice.
        assert fake_client.get_switch_mode.await_count == 1
        assert fake_client.switch_mode.await_count == 2

    async def test_baseline_strips_stale_shim_slots(self, hass, fake_client):
        # Live cube readback has leftover synthetic-price slots from prior runs.
        fake_client.get_switch_mode.return_value = {
            "devId": "5613", "workStatus": "1",
            "selfConsumptioinReserveSoc": "20", "backupPowerReserveSoc": "100",
            "allowChargingXiaGrid": "1",
            "peakTimeList": ["20:00_21:00_1.00"],   # synthetic shim slot
            "midPeakTimeList": [],
            "offPeakTimeList": ["00:30_04:30_0.05"],  # real user slot
            "peakTimeListNonWorkDay": [], "midPeakTimeListNonWorkDay": [],
            "offPeakTimeListNonWorkDay": [],
            "activeWeek": [1, 2, 3, 4, 5], "activeWeekNonWorkDay": [6, 7],
            "dayLightSavingTime": False,
            "dayLightPeakTimeList": [], "dayLightMidPeakTimeList": [],
            "dayLightOffPeakTimeList": [],
            "dayLightPeakTimeListNonWorkDay": [], "dayLightMidPeakTimeListNonWorkDay": [],
            "dayLightOffPeakTimeListNonWorkDay": [],
            "dayLightActiveWeek": [1, 2, 3, 4, 5],
            "dayLightActiveWeekNonWorkDay": [6, 7],
            "touType": 0,
        }
        shim = PredbatShim(hass=hass, entry_id="x", client=fake_client)
        await shim._snapshot_baseline()
        # Stale synthetic peak slot stripped, user's off-peak survives.
        assert shim._baseline["peakTimeList"] == []
        assert shim._baseline["offPeakTimeList"] == ["00:30_04:30_0.05"]

    async def test_patch_baseline_updates_field(self, shim):
        await shim.charge_freeze(end_time=_future(30))
        shim.patch_baseline("selfConsumptioinReserveSoc", "30")
        assert shim._baseline["selfConsumptioinReserveSoc"] == "30"

    async def test_patch_baseline_noop_when_no_baseline(self, shim):
        # User changed a number entity before any Predbat override fired —
        # baseline doesn't exist yet, so patch is a safe no-op.
        shim.patch_baseline("selfConsumptioinReserveSoc", "30")
        assert shim._baseline is None


# ----------------------------------------------------------------------
# Override payload composition
# ----------------------------------------------------------------------
class TestOverridePayloads:
    async def test_charge_writes_off_peak_slot(self, shim, fake_client):
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        body = fake_client.switch_mode.await_args.args[0]
        assert body["workStatus"] == WORK_STATUS_TOU
        assert body["allowChargingXiaGrid"] == "1"
        # Off-peak slot appended at synthetic SHIM_PRICE_OFF_PEAK.
        weekday_key = "offPeakTimeList" if dt_util.as_local(dt_util.utcnow()).weekday() < 5 \
            else "offPeakTimeListNonWorkDay"
        appended = body[weekday_key][-1]
        assert appended.endswith(f"_{SHIM_PRICE_OFF_PEAK:.2f}")

    async def test_discharge_writes_peak_slot_and_blocks_grid_charge(
        self, shim, fake_client
    ):
        await shim.discharge_start(end_time=_future(30), target_soc_pct=10.0)
        body = fake_client.switch_mode.await_args.args[0]
        assert body["workStatus"] == WORK_STATUS_TOU
        assert body["allowChargingXiaGrid"] == "0"   # grid import refused
        weekday_key = "peakTimeList" if dt_util.as_local(dt_util.utcnow()).weekday() < 5 \
            else "peakTimeListNonWorkDay"
        appended = body[weekday_key][-1]
        assert appended.endswith(f"_{SHIM_PRICE_PEAK:.2f}")

    async def test_freeze_writes_mid_peak_slot(self, shim, fake_client):
        await shim.charge_freeze(end_time=_future(30))
        body = fake_client.switch_mode.await_args.args[0]
        assert body["workStatus"] == WORK_STATUS_TOU
        assert body["allowChargingXiaGrid"] == "0"
        weekday_key = "midPeakTimeList" if dt_util.as_local(dt_util.utcnow()).weekday() < 5 \
            else "midPeakTimeListNonWorkDay"
        appended = body[weekday_key][-1]
        assert appended.endswith(f"_{SHIM_PRICE_MID_PEAK:.2f}")


# ----------------------------------------------------------------------
# Revert lifecycle
# ----------------------------------------------------------------------
class TestRevert:
    async def test_charge_stop_reverts_when_active(self, shim, fake_client):
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        await shim.charge_stop()
        # 1 override + 1 revert = 2 writes.
        assert fake_client.switch_mode.await_count == 2
        assert shim.is_active is False

    async def test_charge_stop_noop_when_no_charge_override(self, shim, fake_client):
        await shim.charge_stop()
        fake_client.switch_mode.assert_not_awaited()

    async def test_discharge_stop_does_not_revert_charge(self, shim, fake_client):
        # Wrong-kind stop is a no-op — defends against Predbat sending the
        # "wrong" stop service to a freeze override or similar.
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        await shim.discharge_stop()
        assert shim.is_active is True
        assert fake_client.switch_mode.await_count == 1

    async def test_idle_reverts_any_override(self, shim, fake_client):
        await shim.charge_freeze(end_time=_future(30))
        await shim.idle()
        assert shim.is_active is False

    async def test_idle_noop_when_no_override(self, shim, fake_client):
        await shim.idle()
        fake_client.switch_mode.assert_not_awaited()

    async def test_abandon_override_does_not_write(self, shim, fake_client):
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        assert fake_client.switch_mode.await_count == 1
        shim.abandon_override()
        # Override + baseline both cleared, no revert write.
        assert fake_client.switch_mode.await_count == 1
        assert shim.is_active is False
        assert shim._baseline is None

    async def test_abandon_override_cancels_revert_timer(self, shim):
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        assert shim._revert_unsub is not None
        shim.abandon_override()
        assert shim._revert_unsub is None


# ----------------------------------------------------------------------
# Auto-revert timer
# ----------------------------------------------------------------------
class TestRevertTimer:
    async def test_timer_replaces_prior_schedule(self, shim):
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        first_unsub = shim._revert_unsub
        await shim.discharge_start(end_time=_future(60), target_soc_pct=10.0)
        # New override armed a new timer; the old unsub got called.
        assert shim._revert_unsub is not None
        assert shim._revert_unsub is not first_unsub

    async def test_cancel_revert_timer_clears_handle(self, shim):
        await shim.charge_freeze(end_time=_future(30))
        assert shim._revert_unsub is not None
        shim.cancel_revert_timer()
        assert shim._revert_unsub is None
