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
from unittest.mock import MagicMock

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
        assert payload["dynamicDischargePrice"] == 5.0   # sell above 5p: export now
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
        assert fake_client.switch_mode.await_count == 2
        reverted = _write(fake_client, 1)
        assert reverted["touType"] == 0
        assert "dynamicDischargePrice" not in reverted
        assert reverted["workStatus"] == "1"            # fixture baseline: self-consumption
        assert "touType" in _verify(fake_client, 1)     # a stuck Dynamic mode must be noticed
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
        fake_client.switch_mode.side_effect = [EPCubeError("Failure"), {}]
        await dyn_shim.discharge_stop()
        assert fake_client.switch_mode.await_count == 3
        assert _write(fake_client, 1)["workStatus"] == "2"
        assert _write(fake_client, 2)["workStatus"] == "1"
        assert _write(fake_client, 2)["touType"] == 0
        assert dyn_shim._active_override is None

    async def test_failed_override_write_reverts_and_raises(self, dyn_shim, fake_client):
        fake_client.switch_mode.side_effect = [EPCubeError("Failure"), {}]
        with pytest.raises(EPCubeError):
            await dyn_shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
        assert fake_client.switch_mode.await_count == 2   # override, then the revert
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
        reverted = _write(fake_client, 1)
        assert reverted["touType"] == 1
        assert reverted["dynamicDischargePrice"] == 10.0
        assert reverted["dynamicChargePrice"] == 5.0


class TestDynamicWatchdog:
    async def test_forces_the_cube_out_of_stuck_dynamic_mode(
        self, dyn_shim, fake_client, get_switch_mode
    ):
        fake_client.get_switch_mode.return_value = _dynamic_read(
            get_switch_mode, sell=5.0, buy=0.0
        )
        await dyn_shim._on_watchdog(dt_util.utcnow())
        assert fake_client.switch_mode.await_count == 1
        payload = _write(fake_client, 0)
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
        assert fake_client.switch_mode.await_count == 2   # override + the retried revert
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
