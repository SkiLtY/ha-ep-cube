"""Predbat shim service layer.

Translates Predbat's rate-based, time-windowed control contract into the EP
Cube cloud's price-tier TOU model. See docs/ARCHITECTURE.md for the design
and `<captures-private>/2026-05-20-tou-extract.md` for the wire-level reference.

## Translation model

The real cloud's TOU is economic, not commanded. Slots are labelled
off-peak / mid-peak / peak with prices, and the inverter optimises against
them. Predbat thinks in terms of "charge / hold / discharge" with explicit
windows. The shim maps:

- `charge` → off-peak slot ("charge from grid, not supporting loads"; gated
  by allowChargingXiaGrid=1)
- `discharge` → peak slot ("drain to loads, refuse grid import"). This is
  the best available approximation of Predbat's "force-export" intent —
  the EP Cube cloud has no command for active battery → grid export. Any
  surplus above load may export if `sellingEnable` permits, but it is
  not commanded. See ARCHITECTURE.md → "Known limitation: force-export".
- `hold` / `freeze` → mid-peak slot ("not charging from grid, not
  supporting loads" — genuinely idle, including no solar → battery charging
  during the window). Vendor-confirmed semantics (2026-05-20 TOU capture).

Synthetic prices are in `const.py` (`SHIM_PRICE_*`). They are spaced wide
enough that the inverter never ambiguates the tier ordering.

## Override lifecycle

1. `_snapshot_baseline()` captures the full getSwitchMode payload on the
   first override. Persists for the shim's lifetime.
2. `_apply_override()` dispatches to the right wire endpoint:
   - charge/discharge/freeze → setTimOfUse (mode-switch + schedule in one POST)
3. `_schedule_revert()` arms a one-shot HA timer at end_time.
4. `_revert_to_baseline()` re-applies the baseline mode using the appropriate
   set* endpoint, restoring TOU slots if baseline was in TOU mode.

Idempotency: `_matches_active()` shortcuts any call whose effective parameters
match the live override.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .api import EPCubeClient, EPCubeError, build_slot, build_tou_payload
from .const import (
    DOMAIN,
    OPERATING_MODE_TOU,
    SHIM_PRICE_MID_PEAK,
    SHIM_PRICE_OFF_PEAK,
    SHIM_PRICE_PEAK,
    WORK_STATUS_BACKUP,
    WORK_STATUS_SELF_CONSUMPTION,
    WORK_STATUS_TOU,
)
from .predbat_state import PredbatPlan, PredbatStateError, describe, read_plan

_LOGGER = logging.getLogger(__name__)

# Service names exposed to Predbat
SERVICE_CHARGE_START = "charge_start"
SERVICE_CHARGE_STOP = "charge_stop"
SERVICE_DISCHARGE_START = "discharge_start"
SERVICE_DISCHARGE_STOP = "discharge_stop"
SERVICE_CHARGE_FREEZE = "charge_freeze"
SERVICE_DISCHARGE_FREEZE = "discharge_freeze"
SERVICE_IDLE = "idle"

# Override kinds
KIND_CHARGE = "charge"
KIND_DISCHARGE = "discharge"
KIND_FREEZE = "freeze"

# Predbat shim services accept only an optional device_id. Window/SoC/rate
# parameters come from the Predbat dummy entities (see predbat_state.py).
SHIM_SCHEMA = vol.Schema({vol.Optional("device_id"): cv.string})


class PredbatShim:
    """Per-device Predbat shim. One instance per EP Cube config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str, client: EPCubeClient) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        # Baseline = full getSwitchMode payload snapshotted at first override.
        self._baseline: dict[str, Any] | None = None
        self._active_override: dict[str, Any] | None = None
        self._revert_unsub = None

    @property
    def is_active(self) -> bool:
        return self._active_override is not None

    # ------------------------------------------------------------------
    # Public service handlers
    # ------------------------------------------------------------------
    async def charge_start(self, end_time: datetime, target_soc_pct: float) -> None:
        """Force grid charge until end_time. target_soc_pct is informational —
        the cloud's TOU model has no per-slot SoC target; the inverter uses
        the tier prices to optimise."""
        params = {"kind": KIND_CHARGE, "end_time": end_time, "target_soc_pct": target_soc_pct}
        if self._matches_active(params):
            _LOGGER.debug("charge_start: idempotent no-op")
            return
        await self._snapshot_baseline()
        payload = self._build_tou_override(
            tier="off_peak",
            price=SHIM_PRICE_OFF_PEAK,
            end_time=end_time,
            allow_grid_charge=True,
        )
        await self._apply_tou_override(payload, params)
        self._schedule_revert(end_time)
        _LOGGER.info(
            "charge_start applied: off-peak slot until %s (target_soc=%.0f%% informational)",
            end_time.isoformat(), target_soc_pct,
        )

    async def charge_stop(self) -> None:
        if self._active_override and self._active_override["kind"] == KIND_CHARGE:
            await self._revert_to_baseline()
            _LOGGER.info("charge_stop: reverted to baseline")
        else:
            _LOGGER.debug("charge_stop: no active charge override (no-op)")

    async def discharge_start(self, end_time: datetime, target_soc_pct: float) -> None:
        """Best-effort approximation of Predbat's "force-export" intent. Wires
        a 'peak' tier slot — vendor-confirmed semantics: battery drains to
        loads, grid import refused. The cloud has NO command for active
        battery → grid export; any surplus above load may export if
        `sellingEnable` permits, but it is not commanded. Known limitation —
        see ARCHITECTURE.md."""
        params = {"kind": KIND_DISCHARGE, "end_time": end_time, "target_soc_pct": target_soc_pct}
        if self._matches_active(params):
            _LOGGER.debug("discharge_start: idempotent no-op")
            return
        await self._snapshot_baseline()
        payload = self._build_tou_override(
            tier="peak",
            price=SHIM_PRICE_PEAK,
            end_time=end_time,
            allow_grid_charge=False,
        )
        await self._apply_tou_override(payload, params)
        self._schedule_revert(end_time)
        _LOGGER.info(
            "discharge_start applied: peak slot until %s (target_soc=%.0f%% informational)",
            end_time.isoformat(), target_soc_pct,
        )

    async def discharge_stop(self) -> None:
        if self._active_override and self._active_override["kind"] == KIND_DISCHARGE:
            await self._revert_to_baseline()
            _LOGGER.info("discharge_stop: reverted to baseline")
        else:
            _LOGGER.debug("discharge_stop: no active discharge override (no-op)")

    async def charge_freeze(self, end_time: datetime) -> None:
        """Hold the battery genuinely idle until end_time via a mid-peak TOU
        slot — no grid import, no load support, no solar → battery charging
        during the window. Vendor-confirmed semantics (2026-05-20 TOU capture).
        Auto-revert restores the baseline mode at end_time."""
        params = {"kind": KIND_FREEZE, "end_time": end_time}
        if self._matches_active(params):
            _LOGGER.debug("charge_freeze: idempotent no-op")
            return
        await self._snapshot_baseline()
        payload = self._build_tou_override(
            tier="mid_peak",
            price=SHIM_PRICE_MID_PEAK,
            end_time=end_time,
            allow_grid_charge=False,
        )
        await self._apply_tou_override(payload, params)
        self._schedule_revert(end_time)
        _LOGGER.info("charge_freeze applied: mid-peak slot until %s", end_time.isoformat())

    async def discharge_freeze(self, end_time: datetime) -> None:
        """Alias for charge_freeze — semantics are identical (battery idle)."""
        await self.charge_freeze(end_time)

    async def idle(self) -> None:
        """Restore the baseline. Equivalent to '_stop' for any active override."""
        if self.is_active:
            await self._revert_to_baseline()
            _LOGGER.info("idle: reverted to baseline")
        else:
            _LOGGER.debug("idle: no active override (no-op)")

    def cancel_revert_timer(self) -> None:
        """Public cancel — used during integration teardown."""
        self._cancel_revert()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _matches_active(self, params: dict[str, Any]) -> bool:
        if not self._active_override:
            return False
        for key, value in params.items():
            if self._active_override.get(key) != value:
                return False
        return True

    async def _snapshot_baseline(self) -> None:
        """Capture the full getSwitchMode response (mode + reserves + TOU slots).
        Lazy — runs once per shim lifetime, on first override."""
        if self._baseline is not None:
            return
        try:
            self._baseline = await self.client.get_switch_mode()
        except EPCubeError as err:
            _LOGGER.error("baseline snapshot failed: %s", err)
            raise
        _LOGGER.info(
            "baseline snapshotted: workStatus=%s self_reserve=%s backup_reserve=%s",
            self._baseline.get("workStatus"),
            self._baseline.get("selfConsumptioinReserveSoc"),
            self._baseline.get("backupPowerReserveSoc"),
        )

    def _build_tou_override(
        self,
        *,
        tier: str,            # "off_peak" | "mid_peak" | "peak"
        price: float,         # synthetic — see const.SHIM_PRICE_*
        end_time: datetime,
        allow_grid_charge: bool,
    ) -> dict[str, Any]:
        """Compose a setTimOfUse payload with the override slot appended to
        the baseline's tier lists. The override slot covers now → end_time on
        the current day's profile (weekday or weekend).

        v1 limitation: the override slot is assumed not to span midnight or a
        weekday→weekend transition. Predbat slots are typically 30 min, so
        this holds in practice.
        """
        now_local = dt_util.as_local(dt_util.utcnow())
        end_local = dt_util.as_local(end_time)
        override_slot = build_slot(
            now_local.strftime("%H:%M"),
            end_local.strftime("%H:%M"),
            price,
        )
        is_weekend = now_local.weekday() >= 5
        baseline = self._baseline or {}

        # Start with baseline's tier lists for both profiles. Append our
        # override slot to the appropriate tier in the active profile only.
        def base_tier(key: str) -> list[str]:
            return list(baseline.get(key) or [])

        weekday_off = base_tier("offPeakTimeList")
        weekday_mid = base_tier("midPeakTimeList")
        weekday_peak = base_tier("peakTimeList")
        weekend_off = base_tier("offPeakTimeListNonWorkDay")
        weekend_mid = base_tier("midPeakTimeListNonWorkDay")
        weekend_peak = base_tier("peakTimeListNonWorkDay")

        target_list = {
            ("off_peak", False): weekday_off,
            ("mid_peak", False): weekday_mid,
            ("peak", False): weekday_peak,
            ("off_peak", True): weekend_off,
            ("mid_peak", True): weekend_mid,
            ("peak", True): weekend_peak,
        }[(tier, is_weekend)]
        target_list.append(override_slot)

        return build_tou_payload(
            dev_id=self.client.dev_id,
            weekday_off=_strs_to_tuples(weekday_off),
            weekday_mid=_strs_to_tuples(weekday_mid),
            weekday_peak=_strs_to_tuples(weekday_peak),
            weekend_off=_strs_to_tuples(weekend_off),
            weekend_mid=_strs_to_tuples(weekend_mid),
            weekend_peak=_strs_to_tuples(weekend_peak),
            allow_grid_charge=allow_grid_charge,
            weekday_days=baseline.get("activeWeek") or None,
            weekend_days=baseline.get("activeWeekNonWorkDay") or None,
            dst_active=bool(baseline.get("dayLightSavingTime", False)),
        )

    async def _apply_tou_override(
        self, payload: dict[str, Any], params: dict[str, Any]
    ) -> None:
        """Single POST: setTimOfUse bundles mode-switch with schedule-save."""
        try:
            await self.client.set_tou_schedule(payload)
        except EPCubeError as err:
            _LOGGER.error("setTimOfUse failed: %s", err)
            raise
        self._active_override = {
            **params,
            "applied_mode": OPERATING_MODE_TOU,
            "applied_schedule": payload,
        }

    async def _revert_to_baseline(self) -> None:
        """Restore the snapshotted baseline. Dispatches to the appropriate set*
        endpoint based on baseline workStatus. Cancels any pending revert timer."""
        self._cancel_revert()
        if self._baseline is None:
            _LOGGER.warning("_revert_to_baseline: no baseline available — leaving cloud as-is")
            self._active_override = None
            return
        baseline = self._baseline
        work_status = str(baseline.get("workStatus", WORK_STATUS_SELF_CONSUMPTION))
        try:
            if work_status == WORK_STATUS_SELF_CONSUMPTION:
                await self.client.set_self_consumption(
                    int(baseline.get("selfConsumptioinReserveSoc") or 10)
                )
            elif work_status == WORK_STATUS_BACKUP:
                await self.client.set_backup(
                    int(baseline.get("backupPowerReserveSoc") or 100)
                )
            elif work_status == WORK_STATUS_TOU:
                # Reconstruct the baseline TOU payload from the snapshot and POST.
                payload = build_tou_payload(
                    dev_id=self.client.dev_id,
                    weekday_off=_strs_to_tuples(baseline.get("offPeakTimeList") or []),
                    weekday_mid=_strs_to_tuples(baseline.get("midPeakTimeList") or []),
                    weekday_peak=_strs_to_tuples(baseline.get("peakTimeList") or []),
                    weekend_off=_strs_to_tuples(baseline.get("offPeakTimeListNonWorkDay") or []),
                    weekend_mid=_strs_to_tuples(baseline.get("midPeakTimeListNonWorkDay") or []),
                    weekend_peak=_strs_to_tuples(baseline.get("peakTimeListNonWorkDay") or []),
                    allow_grid_charge=str(baseline.get("allowChargingXiaGrid", "1")) == "1",
                    weekday_days=baseline.get("activeWeek"),
                    weekend_days=baseline.get("activeWeekNonWorkDay"),
                    dst_active=bool(baseline.get("dayLightSavingTime", False)),
                )
                await self.client.set_tou_schedule(payload)
            else:
                _LOGGER.warning("_revert_to_baseline: unknown baseline workStatus=%r — defaulting to self-consumption",
                                work_status)
                await self.client.set_self_consumption(10)
        except EPCubeError as err:
            _LOGGER.error("revert FAILED — battery may be stuck on override. err=%s", err)
            # Leave _active_override so a subsequent retry can target the right state.
            raise
        self._active_override = None

    def _schedule_revert(self, end_time: datetime) -> None:
        """Schedule a one-shot revert at end_time. Replaces any prior schedule."""
        self._cancel_revert()
        when = dt_util.as_utc(end_time)
        self._revert_unsub = async_track_point_in_utc_time(
            self.hass, self._on_revert_timer, when
        )

    def _cancel_revert(self) -> None:
        if self._revert_unsub is not None:
            self._revert_unsub()
            self._revert_unsub = None

    async def _on_revert_timer(self, _now: datetime) -> None:
        _LOGGER.info("revert timer fired — restoring baseline")
        try:
            await self._revert_to_baseline()
        except EPCubeError:
            pass


def _strs_to_tuples(slots: list[str]) -> list[tuple[str, str, float]]:
    """Parse a list of wire-format slot strings into (start, end, price) tuples,
    suitable for passing back into build_tou_payload."""
    out: list[tuple[str, str, float]] = []
    for s in slots:
        try:
            start, end, price = s.split("_")
            out.append((start, end, float(price)))
        except (ValueError, AttributeError):
            _LOGGER.warning("ignoring malformed slot string: %r", s)
    return out


# ---------- service registration ----------


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the Predbat shim services on the HA service bus.

    Handlers read the planned window from `sensor.predbat_<inv>_*` entities via
    predbat_state.read_plan. Service-call args are ignored beyond `device_id`.
    """

    def _resolve_shim(call: ServiceCall) -> PredbatShim:
        shims: dict[str, PredbatShim] = {
            entry_id: data["shim"]
            for entry_id, data in hass.data.get(DOMAIN, {}).items()
            if isinstance(data, dict) and "shim" in data
        }
        if not shims:
            raise vol.Invalid("no EP Cube integrations configured")
        target_device_id = call.data.get("device_id")
        if target_device_id:
            for shim in shims.values():
                if shim.client.dev_id == target_device_id:
                    return shim
            raise vol.Invalid(f"no EP Cube configured for device_id={target_device_id}")
        if len(shims) > 1:
            raise vol.Invalid(
                "multiple EP Cubes configured — pass device_id to disambiguate"
            )
        return next(iter(shims.values()))

    def _read_plan(service: str) -> PredbatPlan:
        try:
            plan = read_plan(hass)
        except PredbatStateError as err:
            _LOGGER.error("%s: cannot read Predbat plan: %s", service, err)
            raise vol.Invalid(f"Predbat plan unreadable: {err}") from err
        _LOGGER.debug("%s: predbat plan = %s", service, describe(plan))
        return plan

    async def handle_charge_start(call: ServiceCall) -> None:
        shim = _resolve_shim(call)
        plan = _read_plan(SERVICE_CHARGE_START)
        if not plan.charge_enabled or plan.charge_window is None:
            _LOGGER.warning(
                "charge_start fired but Predbat reports no active charge plan "
                "(enabled=%s window=%s) — ignoring",
                plan.charge_enabled, plan.charge_window,
            )
            return
        target_soc_pct = float(plan.charge_limit_pct if plan.charge_limit_pct is not None else 100)
        await shim.charge_start(
            end_time=plan.charge_window.end, target_soc_pct=target_soc_pct
        )

    async def handle_charge_stop(call: ServiceCall) -> None:
        await _resolve_shim(call).charge_stop()

    async def handle_discharge_start(call: ServiceCall) -> None:
        shim = _resolve_shim(call)
        plan = _read_plan(SERVICE_DISCHARGE_START)
        if not plan.discharge_enabled or plan.discharge_window is None:
            _LOGGER.warning(
                "discharge_start fired but Predbat reports no active discharge plan "
                "(enabled=%s window=%s) — ignoring",
                plan.discharge_enabled, plan.discharge_window,
            )
            return
        target_soc_pct = float(
            plan.discharge_target_soc_pct if plan.discharge_target_soc_pct is not None else 0
        )
        await shim.discharge_start(
            end_time=plan.discharge_window.end, target_soc_pct=target_soc_pct
        )

    async def handle_discharge_stop(call: ServiceCall) -> None:
        await _resolve_shim(call).discharge_stop()

    async def handle_charge_freeze(call: ServiceCall) -> None:
        shim = _resolve_shim(call)
        plan = _read_plan(SERVICE_CHARGE_FREEZE)
        if plan.charge_window is None:
            _LOGGER.warning(
                "charge_freeze fired but no charge window published — ignoring"
            )
            return
        await shim.charge_freeze(end_time=plan.charge_window.end)

    async def handle_discharge_freeze(call: ServiceCall) -> None:
        shim = _resolve_shim(call)
        plan = _read_plan(SERVICE_DISCHARGE_FREEZE)
        if plan.discharge_window is None:
            _LOGGER.warning(
                "discharge_freeze fired but no discharge window published — ignoring"
            )
            return
        await shim.discharge_freeze(end_time=plan.discharge_window.end)

    async def handle_idle(call: ServiceCall) -> None:
        await _resolve_shim(call).idle()

    hass.services.async_register(DOMAIN, SERVICE_CHARGE_START, handle_charge_start, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_STOP, handle_charge_stop, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCHARGE_START, handle_discharge_start, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCHARGE_STOP, handle_discharge_stop, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_FREEZE, handle_charge_freeze, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCHARGE_FREEZE, handle_discharge_freeze, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_IDLE, handle_idle, schema=SHIM_SCHEMA)


def async_unregister_services(hass: HomeAssistant) -> None:
    for service in (
        SERVICE_CHARGE_START,
        SERVICE_CHARGE_STOP,
        SERVICE_DISCHARGE_START,
        SERVICE_DISCHARGE_STOP,
        SERVICE_CHARGE_FREEZE,
        SERVICE_DISCHARGE_FREEZE,
        SERVICE_IDLE,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
