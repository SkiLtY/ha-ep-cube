"""Tests for the Dynamic-tariff forced export in the Predbat shim.

The EP Cube cloud has no "export now" command, but Dynamic-tariff mode
(workStatus 2 + touType 1) sells to the grid while the export price is above
`dynamicDischargePrice`, stopping at the Reserve SoC. Verified on a real
HES-EU2-S7-20G on 2026-10-14: about 6.4 kW to the grid. With
`force_export_mode == "dynamic"` the shim uses that for Predbat's export
windows.

Like test_shim.py, these run PredbatShim against a fake client (no HTTP) and
assert the call patterns directly.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.ep_cube.api import EPCubeError
from custom_components.ep_cube.const import (
    DEFAULT_FORCE_EXPORT_MODE,
    FORCE_EXPORT_MODE_DYNAMIC,
    FORCE_EXPORT_MODE_TOU_PEAK,
    SHIM_PRICE_PEAK,
    WORK_STATUS_TOU,
)
from custom_components.ep_cube.services import (
    KIND_CHARGE,
    KIND_DISCHARGE,
    PredbatShim,
    _clamp_before_charge_window,
    _clear_dynamic,
    _is_empty_tou,
    _is_shim_dynamic_signature,
)


@pytest.fixture
def shim(hass, fake_client) -> PredbatShim:
    """Default (legacy TOU-peak) shim."""
    instance = PredbatShim(hass=hass, entry_id="test-entry", client=fake_client)
    yield instance
    instance.cancel_revert_timer()


@pytest.fixture
def dyn_shim(hass, fake_client) -> PredbatShim:
    instance = PredbatShim(
        hass=hass,
        entry_id="test-entry",
        client=fake_client,
        force_export_mode=FORCE_EXPORT_MODE_DYNAMIC,
    )
    yield instance
    instance.cancel_revert_timer()


def _future(minutes: int):
    return dt_util.utcnow() + timedelta(minutes=minutes)


def _write(fake_client, n: int) -> dict:
    """Payload of the n-th switch_mode call."""
    return fake_client.switch_mode.await_args_list[n].args[0]


def _verify(fake_client, n: int):
    return fake_client.switch_mode.await_args_list[n].kwargs.get("verify_keys")


def _dynamic_read(base: dict, *, sell: float, buy: float) -> dict:
    return {
        **base,
        "workStatus": "2",
        "touType": 1,
        "dynamicChargePrice": buy,
        "dynamicDischargePrice": sell,
    }


class TestForceExportModeDefault:
    def test_default_is_the_legacy_tou_peak_mode(self, shim):
        assert DEFAULT_FORCE_EXPORT_MODE == FORCE_EXPORT_MODE_TOU_PEAK
        assert shim.force_export_mode == FORCE_EXPORT_MODE_TOU_PEAK

    async def test_legacy_mode_still_writes_a_tou_peak_slot(self, shim, fake_client):
        await shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
        payload = _write(fake_client, 0)
        assert payload["touType"] == 0
        assert any(s.endswith(f"_{SHIM_PRICE_PEAK:.2f}") for s in payload["peakTimeList"])
        assert "dynamicDischargePrice" not in payload


class TestDynamicExport:
    async def test_discharge_start_switches_to_dynamic(self, dyn_shim, fake_client):
        await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=51.0)
        assert fake_client.switch_mode.await_count == 1
        payload = _write(fake_client, 0)
        assert payload["workStatus"] == WORK_STATUS_TOU
        assert payload["touType"] == 1
        assert payload["dynamicDischargePrice"] == 0.0   # sell above 0p: export at any price
        assert payload["dynamicChargePrice"] == 0.0      # never buy for the export
        assert payload["allowChargingXiaGrid"] == "0"
        assert payload["selfConsumptioinReserveSoc"] == "52"
        assert dyn_shim._active_override["kind"] == KIND_DISCHARGE
        assert dyn_shim._active_override["dynamic"] is True

    async def test_write_is_verified_against_dynamic_state(self, dyn_shim, fake_client):
        await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=60.0)
        keys = _verify(fake_client, 0)
        for key in (
            "workStatus", "touType", "dynamicDischargePrice", "selfConsumptioinReserveSoc",
        ):
            assert key in keys

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            (51.0, 52),    # rounded UP to even: never export below Predbat's target
            (50.0, 50),
            (49.1, 50),
            (100.0, 100),
            (150.0, 100),  # clamped
            (5.0, 20),     # never below the cube's own self-consumption reserve
            (0.0, 20),
        ],
    )
    async def test_reserve_follows_predbat_target(
        self, dyn_shim, fake_client, target, expected
    ):
        await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=target)
        assert _write(fake_client, 0)["selfConsumptioinReserveSoc"] == str(expected)

    async def test_idempotent_within_the_same_reserve_step(self, dyn_shim, fake_client):
        end = _future(30)
        await dyn_shim.discharge_start(end_time=end, target_soc_pct=51.0)  # reserve 52
        await dyn_shim.discharge_start(end_time=end, target_soc_pct=52.0)  # reserve 52
        assert fake_client.switch_mode.await_count == 1
        await dyn_shim.discharge_start(end_time=end, target_soc_pct=55.0)  # reserve 56
        assert fake_client.switch_mode.await_count == 2

    async def test_revert_restores_standard_tou_and_checks_it(self, dyn_shim, fake_client):
        await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
        await dyn_shim.discharge_stop()
        assert fake_client.switch_mode.await_count == 3
        hold = _write(fake_client, 1)
        assert hold["touType"] == 1                      # still Dynamic: safe thresholds first
        assert hold["dynamicChargePrice"] == 0.0 and hold["dynamicDischargePrice"] == 999.99
        assert hold["backupPowerReserveSoc"] == "100"    # the baseline's Charging Limit
        reverted = _write(fake_client, 2)
        assert reverted["touType"] == 0
        assert "dynamicDischargePrice" not in reverted
        assert reverted["workStatus"] == "1"            # fixture baseline: self-consumption
        assert "touType" in _verify(fake_client, 2)     # a stuck Dynamic mode must be noticed
        assert dyn_shim._active_override is None

    async def test_a_failed_hold_step_does_not_stop_the_revert(self, dyn_shim, fake_client):
        await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
        fake_client.switch_mode.side_effect = [EPCubeError("Failure"), {}]
        await dyn_shim.discharge_stop()
        assert fake_client.switch_mode.await_count == 3      # override, failed hold, baseline
        assert _write(fake_client, 2)["touType"] == 0
        assert dyn_shim._active_override is None

    async def test_revert_to_empty_tou_falls_back_to_self_consumption(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        # The app and cloud reject a switch to TOU with no slots; an empty TOU
        # schedule behaves as self-consumption anyway.
        empty_tou = {
            **get_switch_mode,
            "workStatus": "2",
            "offPeakTimeList": [],
            "midPeakTimeList": [],
            "peakTimeList": [],
        }
        fake_client.get_switch_mode.return_value = empty_tou
        await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
        # hold step ok, the empty-TOU baseline write rejected, then the fallback
        fake_client.switch_mode.side_effect = [{}, EPCubeError("Failure"), {}]
        await dyn_shim.discharge_stop()
        assert fake_client.switch_mode.await_count == 4
        assert _write(fake_client, 1)["touType"] == 1
        assert _write(fake_client, 2)["workStatus"] == "2"
        assert _write(fake_client, 3)["workStatus"] == "1"
        assert _write(fake_client, 3)["touType"] == 0
        assert dyn_shim._active_override is None

    async def test_failed_override_write_reverts_and_raises(self, dyn_shim, fake_client):
        fake_client.switch_mode.side_effect = [EPCubeError("Failure"), {}, {}]
        with pytest.raises(EPCubeError):
            await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
        assert fake_client.switch_mode.await_count == 3   # override, then the two-step revert
        assert dyn_shim._active_override is None

    async def test_failed_revert_is_retried(self, dyn_shim, fake_client):
        await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
        fake_client.switch_mode.side_effect = EPCubeError("cloud down")
        await dyn_shim._on_revert_timer(dt_util.utcnow())
        assert dyn_shim._retry_unsub is not None
        assert dyn_shim._revert_retries == 1
        assert dyn_shim._active_override is not None     # still believed active

    async def test_debug_dynamic_export_uses_the_given_reserve(self, shim, fake_client):
        # Works whatever the configured mode: it is the manual live-test hook.
        await shim.debug_dynamic_export(5, 60.0)
        payload = _write(fake_client, 0)
        assert payload["touType"] == 1
        assert payload["selfConsumptioinReserveSoc"] == "60"


class TestDynamicBaselineHandling:
    async def test_stale_shim_dynamic_export_is_not_adopted(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        # HA restarted mid-window: the cube is still in our forced-export mode.
        fake_client.get_switch_mode.return_value = _dynamic_read(
            get_switch_mode, sell=5.0, buy=0.0
        )
        await dyn_shim._snapshot_baseline()
        assert dyn_shim._baseline["touType"] == 0
        assert "dynamicDischargePrice" not in dyn_shim._baseline

    async def test_stale_export_reserve_falls_back_to_the_default(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        # The reserve a forced export left behind is Predbat's target (here
        # 50%), not the user's setting, so it must not be restored.
        stale = _dynamic_read(get_switch_mode, sell=5.0, buy=0.0)
        stale["selfConsumptioinReserveSoc"] = "50"
        fake_client.get_switch_mode.return_value = stale
        await dyn_shim._snapshot_baseline()
        assert dyn_shim._baseline["touType"] == 0
        assert dyn_shim._baseline["selfConsumptioinReserveSoc"] == "20"

    async def test_users_own_dynamic_tariff_is_kept(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        fake_client.get_switch_mode.return_value = _dynamic_read(
            get_switch_mode, sell=10.0, buy=5.0
        )
        await dyn_shim._snapshot_baseline()
        assert dyn_shim._baseline["touType"] == 1
        assert dyn_shim._baseline["dynamicDischargePrice"] == 10.0

    async def test_tou_override_forces_standard_touType(
        self, shim, fake_client, get_switch_mode
    ):
        # Regression for the HTTP 500 "Failure" reported on a cube that has
        # Dynamic tariff enabled: mirroring touType 1 from the read-back into
        # a slot-based TOU write is rejected by the cloud.
        fake_client.get_switch_mode.return_value = _dynamic_read(
            get_switch_mode, sell=10.0, buy=5.0
        )
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        payload = _write(fake_client, 0)
        assert payload["workStatus"] == WORK_STATUS_TOU
        assert payload["touType"] == 0
        assert "dynamicDischargePrice" not in payload
        assert "dynamicChargePrice" not in payload

    async def test_users_dynamic_tariff_comes_back_after_a_tou_override(
        self, shim, fake_client, get_switch_mode
    ):
        fake_client.get_switch_mode.return_value = _dynamic_read(
            get_switch_mode, sell=10.0, buy=5.0
        )
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        await shim.charge_stop()
        # Three writes: the override, then a two-step revert. Restoring the
        # user's lists and touType 1 in ONE write is the shape the cloud
        # rejects (issue #1), so the lists go back with touType 0 first.
        assert fake_client.switch_mode.await_count == 3
        tou_step = _write(fake_client, 1)
        assert tou_step["touType"] == 0
        assert "dynamicDischargePrice" not in tou_step
        assert _verify(fake_client, 1) == ("workStatus", "touType")
        reverted = _write(fake_client, 2)
        assert reverted["touType"] == 1
        assert reverted["dynamicDischargePrice"] == 10.0
        assert reverted["dynamicChargePrice"] == 5.0
        assert "dynamicDischargePrice" in _verify(fake_client, 2)

    async def test_failed_dynamic_restore_is_retried_from_the_tou_step(
        self, shim, fake_client, get_switch_mode
    ):
        fake_client.get_switch_mode.return_value = _dynamic_read(
            get_switch_mode, sell=10.0, buy=5.0
        )
        await shim.charge_start(end_time=_future(30), target_soc_pct=90.0)
        # Step 1 (TOU lists, touType 0) succeeds; step 2 (back to Dynamic) fails.
        fake_client.switch_mode.side_effect = [{}, EPCubeError("cloud down")]
        await shim._on_revert_timer(dt_util.utcnow())
        assert shim._retry_unsub is not None
        assert shim._revert_retries == 1
        assert shim._active_override is not None
        # The retry runs both steps again from the top and then completes.
        fake_client.switch_mode.side_effect = None
        await shim._on_revert_retry(dt_util.utcnow())
        assert shim._active_override is None
        assert _write(fake_client, 3)["touType"] == 0     # step 1 again
        assert _write(fake_client, 4)["touType"] == 1     # step 2 succeeds


class TestDynamicWatchdog:
    async def test_forces_the_cube_out_of_stuck_dynamic_mode(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        fake_client.get_switch_mode.return_value = _dynamic_read(
            get_switch_mode, sell=5.0, buy=0.0
        )
        await dyn_shim._on_watchdog(dt_util.utcnow())
        assert fake_client.switch_mode.await_count == 2      # safe hold, then the baseline
        assert _write(fake_client, 0)["dynamicDischargePrice"] == 999.99
        payload = _write(fake_client, 1)
        assert payload["touType"] == 0
        assert "dynamicDischargePrice" not in payload

    async def test_leaves_a_healthy_cube_alone(self, dyn_shim, fake_client):
        await dyn_shim._on_watchdog(dt_util.utcnow())
        fake_client.switch_mode.assert_not_awaited()

    async def test_leaves_a_users_own_dynamic_tariff_alone(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        fake_client.get_switch_mode.return_value = _dynamic_read(
            get_switch_mode, sell=10.0, buy=5.0
        )
        await dyn_shim._on_watchdog(dt_util.utcnow())
        fake_client.switch_mode.assert_not_awaited()

    async def test_retries_a_revert_that_never_completed(self, dyn_shim, fake_client):
        await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
        assert dyn_shim._active_override is not None
        await dyn_shim._on_watchdog(dt_util.utcnow())
        assert fake_client.switch_mode.await_count == 3   # override + the retried two-step revert
        assert dyn_shim._active_override is None


class TestDynamicHelpers:
    def test_signature_matches_only_the_shim_thresholds(self, get_switch_mode):
        assert _is_shim_dynamic_signature(_dynamic_read(get_switch_mode, sell=5.0, buy=0.0))
        assert not _is_shim_dynamic_signature(
            _dynamic_read(get_switch_mode, sell=10.0, buy=5.0)
        )
        assert not _is_shim_dynamic_signature(get_switch_mode)  # touType 0
        assert not _is_shim_dynamic_signature({"touType": 1})   # no thresholds

    def test_clear_dynamic(self, get_switch_mode):
        cleared = _clear_dynamic(_dynamic_read(get_switch_mode, sell=5.0, buy=0.0))
        assert cleared["touType"] == 0
        assert "dynamicChargePrice" not in cleared
        assert "dynamicDischargePrice" not in cleared

    def test_empty_tou_detection(self, get_switch_mode):
        assert not _is_empty_tou(get_switch_mode)  # self-consumption with slots
        empty = {"workStatus": "2", "offPeakTimeList": [], "peakTimeList": []}
        assert _is_empty_tou(empty)
        assert not _is_empty_tou({**empty, "workStatus": "1"})
        assert not _is_empty_tou({**empty, "midPeakTimeListNonWorkDay": ["14:30_15:30_"]})


class TestClampBeforeChargeWindow:
    @staticmethod
    def _plan(*, charge_in_minutes, enabled=True):
        window = MagicMock()
        window.start = _future(charge_in_minutes)
        window.end = _future(charge_in_minutes + 300)
        plan = MagicMock()
        plan.charge_enabled = enabled
        plan.charge_window = window
        return plan

    def test_ends_at_the_charge_start_when_they_overlap(self):
        plan = self._plan(charge_in_minutes=20)
        assert _clamp_before_charge_window(plan, _future(60)) == plan.charge_window.start

    def test_unchanged_when_the_charge_is_after_the_export(self):
        plan = self._plan(charge_in_minutes=120)
        end = _future(60)
        assert _clamp_before_charge_window(plan, end) == end

    def test_unchanged_without_a_charge_plan(self):
        plan = self._plan(charge_in_minutes=20, enabled=False)
        end = _future(60)
        assert _clamp_before_charge_window(plan, end) == end


# ----------------------------------------------------------------------
# Sell-above guard: the threshold must sit under the window's export prices
# ----------------------------------------------------------------------
def _export_slots(prices):
    """Half-hour export slots starting 10 minutes ago, one per price."""
    start = dt_util.utcnow() - timedelta(minutes=10)
    return [
        {"start": start + timedelta(minutes=30 * i),
         "end": start + timedelta(minutes=30 * (i + 1)), "price": p}
        for i, p in enumerate(prices)
    ]


@pytest.fixture
def priced_shim(hass, fake_client):
    coordinator = MagicMock()
    coordinator.data = {"import": [], "export": []}
    instance = PredbatShim(
        hass=hass, entry_id="test-entry", client=fake_client,
        force_export_mode=FORCE_EXPORT_MODE_DYNAMIC, price_coordinator=coordinator,
    )
    instance.coordinator = coordinator
    yield instance
    instance.cancel_revert_timer()


class TestSellAboveGuard:
    def _sell_above(self, shim, prices, minutes=60):
        shim.coordinator.data = {"import": [], "export": _export_slots(prices)}
        return shim._sell_above_for_window(_future(minutes))

    async def test_the_threshold_is_always_zero(self, priced_shim):
        assert self._sell_above(priced_shim, [11.3, 12.0]) == 0.0
        assert self._sell_above(priced_shim, [0.5, 9.0]) == 0.0
        assert self._sell_above(priced_shim, [-3.0]) == 0.0

    async def test_a_zero_or_negative_price_in_the_window_warns(self, priced_shim, caplog):
        self._sell_above(priced_shim, [5.0, 0.0])
        assert "will not export in that slot" in caplog.text

    async def test_ordinary_prices_do_not_warn(self, priced_shim, caplog):
        self._sell_above(priced_shim, [5.0, 11.0])
        assert "will not export in that slot" not in caplog.text

    async def test_only_slots_inside_the_window_count(self, priced_shim, caplog):
        # A zero-priced slot after the window ends must not warn.
        prices = [12.0, 12.0, 0.0]            # third slot starts at +50 min
        self._sell_above(priced_shim, prices, minutes=40)
        assert "will not export in that slot" not in caplog.text

    async def test_no_prices_is_fine(self, hass, fake_client):
        plain = PredbatShim(hass=hass, entry_id="e", client=fake_client,
                            force_export_mode=FORCE_EXPORT_MODE_DYNAMIC)
        assert plain._sell_above_for_window(_future(30)) == 0.0

    async def test_written_payload_uses_zero(self, priced_shim, fake_client):
        priced_shim.coordinator.data = {"import": [], "export": _export_slots([3.0, 3.0])}
        await priced_shim.discharge_start(end_time=_future(50), target_soc_pct=50.0)
        payload = fake_client.switch_mode.await_args_list[-1].args[0]
        assert payload["dynamicDischargePrice"] == 0.0
        assert payload["dynamicChargePrice"] == 0.0

    async def test_stale_exports_are_recognised_whatever_build_wrote_them(
        self, get_switch_mode
    ):
        for sell in (0.0, 0.01, 2.0, 5.0):
            assert _is_shim_dynamic_signature(_dynamic_read(get_switch_mode, sell=sell, buy=0.0))
        assert not _is_shim_dynamic_signature(_dynamic_read(get_switch_mode, sell=999.99, buy=0.0))
        assert not _is_shim_dynamic_signature(_dynamic_read(get_switch_mode, sell=5.5, buy=0.0))

class TestDynamicCharge:
    async def test_writes_a_dynamic_charge(self, dyn_shim, fake_client):
        await dyn_shim.debug_dynamic_charge(duration_minutes=5, limit_pct=72.0)
        payload = _write(fake_client, 0)
        assert payload["workStatus"] == WORK_STATUS_TOU and payload["touType"] == 1
        assert payload["dynamicChargePrice"] == 999.99     # always buy
        assert payload["dynamicDischargePrice"] == 999.99  # never sell
        assert payload["backupPowerReserveSoc"] == "72"    # Charging Limit SoC
        assert payload["allowChargingXiaGrid"] == "1"
        assert payload["selfConsumptioinReserveSoc"] == "20"   # the baseline's, untouched
        assert dyn_shim._active_override["kind"] == KIND_CHARGE
        assert dyn_shim._active_override["dynamic"] is True
        keys = _verify(fake_client, 0)
        for key in ("dynamicChargePrice", "backupPowerReserveSoc", "dynamicDischargePrice"):
            assert key in keys

    async def test_limit_is_rounded_up_and_clamped(self, dyn_shim, fake_client):
        await dyn_shim.debug_dynamic_charge(duration_minutes=5, limit_pct=71.2)
        assert _write(fake_client, 0)["backupPowerReserveSoc"] == "72"

    async def test_reverts_to_the_baseline_and_restores_the_charging_limit(
        self, dyn_shim, fake_client
    ):
        await dyn_shim.debug_dynamic_charge(duration_minutes=5, limit_pct=72.0)
        await dyn_shim._on_revert_timer(dt_util.utcnow())
        assert dyn_shim._active_override is None
        hold = fake_client.switch_mode.await_args_list[-2].args[0]
        assert hold["touType"] == 1                          # still Dynamic, but safe
        assert hold["dynamicChargePrice"] == 0.0 and hold["dynamicDischargePrice"] == 999.99
        assert hold["backupPowerReserveSoc"] == "100"        # the test's 72 is gone
        restored = fake_client.switch_mode.await_args_list[-1].args[0]
        assert restored["touType"] == 0
        assert restored["backupPowerReserveSoc"] == "100"   # the baseline's

    async def test_a_stale_charge_is_recognised_and_its_limit_reset(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        stale = _dynamic_read(get_switch_mode, sell=999.99, buy=999.99)
        stale["backupPowerReserveSoc"] = "72"
        stale["selfConsumptioinReserveSoc"] = "35"      # the user's own, must survive
        fake_client.get_switch_mode.return_value = stale
        assert _is_shim_dynamic_signature(stale)
        await dyn_shim._snapshot_baseline()
        assert dyn_shim._baseline["touType"] == 0
        assert dyn_shim._baseline["backupPowerReserveSoc"] == "100"
        assert dyn_shim._baseline["selfConsumptioinReserveSoc"] == "35"

    async def test_the_hold_setting_is_not_mistaken_for_a_charge(self, get_switch_mode):
        hold = _dynamic_read(get_switch_mode, sell=999.99, buy=0.0)
        assert not _is_shim_dynamic_signature(hold)


    async def test_leftover_charge_thresholds_reset_the_charging_limit(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        # Not in Dynamic mode, but the cloud still holds a charge's thresholds and
        # its Charging Limit (the state a cube is left in by an older revert).
        leftover = {**get_switch_mode, "touType": 0, "dynamicChargePrice": 999.99,
                    "dynamicDischargePrice": 999.99, "backupPowerReserveSoc": "89"}
        fake_client.get_switch_mode.return_value = leftover
        await dyn_shim._snapshot_baseline()
        assert dyn_shim._baseline["backupPowerReserveSoc"] == "100"

    async def test_ordinary_thresholds_leave_the_charging_limit_alone(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        state = {**get_switch_mode, "touType": 0, "dynamicChargePrice": 0.0,
                 "dynamicDischargePrice": 999.99, "backupPowerReserveSoc": "90"}
        fake_client.get_switch_mode.return_value = state
        await dyn_shim._snapshot_baseline()
        assert dyn_shim._baseline["backupPowerReserveSoc"] == "90"


    async def test_sell_above_override_is_written_exactly(self, dyn_shim, fake_client):
        await dyn_shim.debug_dynamic_export(5, 60.0, 0.0)
        assert _write(fake_client, 0)["dynamicDischargePrice"] == 0.0
        assert _write(fake_client, 0)["selfConsumptioinReserveSoc"] == "60"

    async def test_without_an_override_the_guard_picks_the_threshold(self, dyn_shim, fake_client):
        await dyn_shim.debug_dynamic_export(5, 60.0)
        assert _write(fake_client, 0)["dynamicDischargePrice"] == 0.0


# ----------------------------------------------------------------------
# The Dynamic option for Predbat's charge windows
# ----------------------------------------------------------------------
NOTIFY = "custom_components.ep_cube.services.persistent_notification.async_create"


def _price_data(*, imp=True, exp=True):
    """Cube price slots covering now (10 minutes ago to +50), or none."""
    return {
        "import": _export_slots([25.0]) if imp else [],
        "export": _export_slots([12.0]) if exp else [],
    }


@pytest.fixture
def charge_shim(hass, fake_client):
    from custom_components.ep_cube.const import FORCE_CHARGE_MODE_DYNAMIC

    prices = MagicMock()
    prices.data = _price_data()
    status = MagicMock()
    status.data = MagicMock(soc_pct=60.0, grid_power_w=0.0, battery_power_w=0.0)
    instance = PredbatShim(
        hass=hass, entry_id="test-entry", client=fake_client,
        force_export_mode=FORCE_EXPORT_MODE_DYNAMIC,
        force_charge_mode=FORCE_CHARGE_MODE_DYNAMIC,
        price_coordinator=prices, status_coordinator=status,
    )
    instance.prices, instance.status = prices, status
    yield instance
    instance.cancel_revert_timer()


class TestDynamicChargeOption:
    async def test_charge_start_becomes_a_dynamic_charge_to_predbats_target(
        self, charge_shim, fake_client
    ):
        await charge_shim.charge_start(end_time=_future(60), target_soc_pct=72.4)
        payload = _write(fake_client, 0)
        assert payload["touType"] == 1 and payload["workStatus"] == WORK_STATUS_TOU
        assert payload["dynamicChargePrice"] == 999.99      # always buy
        assert payload["dynamicDischargePrice"] == 999.99   # never sell
        assert payload["backupPowerReserveSoc"] == "73"     # the target, rounded up
        assert charge_shim._active_override["kind"] == KIND_CHARGE

    async def test_the_default_mode_still_uses_a_tou_slot(self, hass, fake_client):
        plain = PredbatShim(hass=hass, entry_id="e", client=fake_client)
        await plain.charge_start(end_time=_future(60), target_soc_pct=80.0)
        payload = _write(fake_client, 0)
        assert payload["touType"] == 0
        assert "dynamicChargePrice" not in payload
        plain.cancel_revert_timer()

    async def test_charge_stop_reverts_with_the_safe_hold_first(self, charge_shim, fake_client):
        await charge_shim.charge_start(end_time=_future(60), target_soc_pct=80.0)
        await charge_shim.charge_stop()
        assert fake_client.switch_mode.await_count == 3          # override, hold, baseline
        assert _write(fake_client, 1)["dynamicChargePrice"] == 0.0
        assert _write(fake_client, 2)["touType"] == 0
        assert charge_shim._active_override is None

    # --- before the window: does the cube have a price? ----------------
    async def test_no_cube_price_raises_a_notification_but_still_writes(
        self, charge_shim, fake_client
    ):
        charge_shim.prices.data = _price_data(imp=False)
        with patch(NOTIFY) as notify:
            await charge_shim.charge_start(end_time=_future(60), target_soc_pct=80.0)
        notify.assert_called_once()
        assert "no import price" in notify.call_args.args[1]
        assert fake_client.switch_mode.await_count == 1

    async def test_a_cube_price_means_no_notification(self, charge_shim):
        with patch(NOTIFY) as notify:
            await charge_shim.charge_start(end_time=_future(60), target_soc_pct=80.0)
        notify.assert_not_called()

    async def test_prices_not_fetched_yet_means_no_notification(self, charge_shim):
        charge_shim.prices.data = None
        with patch(NOTIFY) as notify:
            await charge_shim.charge_start(end_time=_future(60), target_soc_pct=80.0)
        notify.assert_not_called()

    async def test_export_also_checks_for_a_cube_price(self, charge_shim):
        charge_shim.prices.data = _price_data(exp=False)
        with patch(NOTIFY) as notify:
            await charge_shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
        notify.assert_called_once()
        assert "no export price" in notify.call_args.args[1]

    # --- during the window: did it start charging? ---------------------
    async def test_check_resends_once_when_the_battery_is_not_charging(
        self, charge_shim, fake_client
    ):
        await charge_shim.charge_start(end_time=_future(60), target_soc_pct=80.0)
        with patch(NOTIFY) as notify:
            await charge_shim._on_charge_check(dt_util.utcnow())
        notify.assert_called_once()
        assert fake_client.switch_mode.await_count == 2          # override + the re-send
        assert _write(fake_client, 1)["backupPowerReserveSoc"] == "80"

    async def test_check_is_quiet_when_charging_from_the_grid(self, charge_shim, fake_client):
        await charge_shim.charge_start(end_time=_future(60), target_soc_pct=80.0)
        charge_shim.status.data = MagicMock(soc_pct=65.0, grid_power_w=6000.0, battery_power_w=6500.0)
        with patch(NOTIFY) as notify:
            await charge_shim._on_charge_check(dt_util.utcnow())
        notify.assert_not_called()
        assert fake_client.switch_mode.await_count == 1

    async def test_check_is_quiet_once_the_target_is_reached(self, charge_shim, fake_client):
        await charge_shim.charge_start(end_time=_future(60), target_soc_pct=80.0)
        charge_shim.status.data = MagicMock(soc_pct=79.5, grid_power_w=0.0, battery_power_w=0.0)
        with patch(NOTIFY) as notify:
            await charge_shim._on_charge_check(dt_util.utcnow())
        notify.assert_not_called()
        assert fake_client.switch_mode.await_count == 1

    async def test_check_does_nothing_when_no_charge_is_active(self, charge_shim, fake_client):
        with patch(NOTIFY) as notify:
            await charge_shim._on_charge_check(dt_util.utcnow())
        notify.assert_not_called()
        fake_client.switch_mode.assert_not_awaited()

    async def test_check_is_only_armed_for_windows_longer_than_the_delay(self, charge_shim):
        await charge_shim.charge_start(end_time=_future(3), target_soc_pct=80.0)
        assert charge_shim._charge_check_unsub is None
        await charge_shim.charge_stop()
        await charge_shim.charge_start(end_time=_future(60), target_soc_pct=80.0)
        assert charge_shim._charge_check_unsub is not None
        await charge_shim.charge_stop()
        assert charge_shim._charge_check_unsub is None
