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
  supporting loads": no grid charging and no discharge to the house). Solar
  can still charge the battery (seen on a real cube, 2026-10-20), so it
  cannot deliver Predbat's "freeze export".

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
import math
import re
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .api import (
    EPCubeClient,
    EPCubeError,
    build_slot,
    payload_from_switch_mode_read,
)
from .const import (
    DEFAULT_FORCE_CHARGE_MODE,
    DEFAULT_FORCE_EXPORT_MODE,
    DYNAMIC_CHARGE_CHECK_MINUTES,
    FORCE_CHARGE_MODE_DYNAMIC,
    DOMAIN,
    DEFAULT_SELF_CONSUMPTION_RESERVE_PCT,
    DEFAULT_CHARGING_LIMIT_PCT,
    DYNAMIC_CHARGE_BUY_BELOW_P,
    DYNAMIC_CHARGE_SELL_ABOVE_P,
    DYNAMIC_EXPORT_BUY_BELOW_P,
    DYNAMIC_HOLD_BUY_BELOW_P,
    DYNAMIC_HOLD_SELL_ABOVE_P,
    DYNAMIC_EXPORT_LEGACY_SELL_ABOVE_MAX_P,
    DYNAMIC_EXPORT_SELL_ABOVE_P,
    DYNAMIC_REVERT_MAX_RETRIES,
    DYNAMIC_REVERT_RETRY_SECONDS,
    DYNAMIC_REVERT_WATCHDOG_MINUTES,
    FORCE_EXPORT_MODE_DYNAMIC,
    OPERATING_MODE_SELF_CONSUMPTION,
    OPERATING_MODE_TOU,
    SHIM_PRICE_MID_PEAK,
    SHIM_PRICE_OFF_PEAK,
    SHIM_PRICE_PEAK,
    TOU_TYPE_DYNAMIC,
    TOU_TYPE_STANDARD,
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
SERVICE_DEBUG_FREEZE = "debug_freeze"
SERVICE_DEBUG_DYNAMIC_EXPORT = "debug_dynamic_export"
SERVICE_DEBUG_DYNAMIC_CHARGE = "debug_dynamic_charge"
SERVICE_DEBUG_PROBE = "debug_probe"
SERVICE_DEBUG_PROBE_WRITE = "debug_probe_write"

# Override kinds
KIND_CHARGE = "charge"
KIND_DISCHARGE = "discharge"
KIND_FREEZE = "freeze"

# Predbat shim services accept only an optional device_id. Window/SoC/rate
# parameters come from the Predbat dummy entities (see predbat_state.py).
SHIM_SCHEMA = vol.Schema({vol.Optional("device_id"): cv.string})

# debug_freeze bypasses read_plan — takes a duration directly. Diagnostic
# tool for manually exercising the shim against the live cube.
DEBUG_FREEZE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Optional("duration_minutes", default=5): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=60)
        ),
    }
)

# debug_dynamic_export bypasses read_plan too: a forced Dynamic-tariff export
# for a short, bounded time, stopping at the given Reserve SoC. For exercising
# the new path against the live cube before Predbat is allowed to use it.
DEBUG_DYNAMIC_EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Optional("duration_minutes", default=5): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=60)
        ),
        vol.Optional("reserve_pct", default=50): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        # Test hook: write this exact sell-above (for example 0.00 to learn whether
        # the cube reads 0 as "selling off") instead of the one the guard picks.
        vol.Optional("sell_above"): vol.All(vol.Coerce(float), vol.Range(min=0, max=5)),
    }
)

# debug_dynamic_charge: an experiment, bounded to 15 minutes. Dynamic-tariff mode
# with buy-below at the maximum, so the cube charges from the grid up to the
# Charging Limit SoC, then the baseline is restored.
DEBUG_DYNAMIC_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Optional("duration_minutes", default=5): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=15)
        ),
        vol.Required("limit_pct"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    }
)

# debug_probe: read-only GETs against candidate cloud routes, to find where
# data such as the supplier / tariff lives. Whitelisted, so the service can
# never reach a write route or a personal-data route (addresses, e-mail).
PROBE_ROUTES = (
    "device/getSettingsData",
    "device/getEarningsConfig",
    "device/queryPriceDataGraphV2",
    "device/getNewestModeInfo",
    "device/getSolarPvPower",
    "device/queryDataGraphV2",
    "device/getDevPowerCutLog",
    "device/netWorkInfo",
    "device/getWarranty",
    # dynamicPrice/*: the country / region / supplier / tariff the cube is
    # set to use for Dynamic mode, and the lists the app picks them from.
    "dynamicPrice/getRelation",
    "dynamicPrice/getCountry",
    "dynamicPrice/getRegionList",
    "dynamicPrice/getBrands",
    "dynamicPrice/getProducts",
    "dynamicPrice/calcuPrice",
    # vpp/*: read-style routes only. Registration, commissioning, site auth,
    # flip/updateSoc and the address / user-info routes are left out.
    "vpp/allPrograms",
    "vpp/enrollments",
    "vpp/events",
    "vpp/event",
    "vpp/globalEnrollmentStatus",
    "vpp/site/programs",
    "vpp/site/program",
    "vpp/energyhub/program",
    "vpp/energyhub/events",
    "vpp/energyhub/enrollment",
    "vpp/flip/getGlobalSoc",
)
PROBE_PARAM_KEYS = (
    "devId", "sgSn", "queryDateStr", "scopeType", "dateStr",
    "supportCountryId", "regionCode", "region", "brandName", "productType",
    "brandId", "dynamicChargePrice", "checkTime",
)
PROBE_MAX_LIST = 20
_PROBE_REDACT = re.compile(
    r"mail|address|phone|token|password|postcode|user|ssid|wifiname"
    r"|^(name|lat|lng|lon|latitude|longitude)$",
    re.IGNORECASE,
)

DEBUG_PROBE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("route"): vol.In(PROBE_ROUTES),
        vol.Optional("params", default=dict): vol.Schema(
            {vol.In(PROBE_PARAM_KEYS): cv.string}
        ),
        vol.Optional("full", default=False): cv.boolean,
    }
)


# debug_probe_write: find the mobile route that saves the Dynamic-tariff
# binding (country / region / supplier / tariff). The portal's own route
# (home/setDynamicPrice) answers 500. The body is never user-supplied: it is
# built from the cube's CURRENT settings, so a route that works re-saves what
# is already there. dynamicPrice/saveBinding is the route the Android app
# (3.2.0) declares for this; the next sends the usual switchMode body with the
# portal's `dynamicPriceBinding` block added; the rest guess the route name
# and send the portal's body.
PROBE_WRITE_ROUTES = (
    "dynamicPrice/saveBinding",
    "device/switchMode",
    "device/setDynamicPrice",
    "device/saveDynamicPrice",
    "device/setDynamicPriceBinding",
    "dynamicPrice/setRelation",
    "dynamicPrice/saveRelation",
    "dynamicPrice/setDynamicPrice",
    "dynamicPrice/save",
    "dynamicPrice/bind",
)

# The only value a caller may change: the export product, from the two codes
# the cloud lists for region L. Lets a test show whether a route really saves
# the binding (re-sending the current value can't tell).
PROBE_EXPORT_PRODUCTS = ("AGILE-OUTGOING-19-05-13", "OUTGOING-VAR-24-10-26")

DEBUG_PROBE_WRITE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("route"): vol.In(PROBE_WRITE_ROUTES),
        vol.Optional("export_product"): vol.In(PROBE_EXPORT_PRODUCTS),
    }
)


def _binding_from_relation(dev_id: str, rel: dict[str, Any]) -> dict[str, Any]:
    """The portal's `dynamicPriceBinding` block, from getRelation's read-back."""
    return {
        "brandId": str(rel.get("brandId", "")),
        "devId": str(dev_id),
        "productBrand": rel.get("productBrand", ""),
        "supportCountryId": str(rel.get("supportCountryId", "")),
        "productCode": rel.get("productCode", ""),
        "productCodeOut": rel.get("productCodeOut", ""),
        "regionCode": rel.get("regionCode") or rel.get("region", ""),
        "ratio": {"fraunhofer": {
            "standardImportRatio": 0, "importServiceCharge": 0,
            "standardExportRatio": 0, "exportServiceCharge": 0,
        }},
    }


def build_probe_write_body(
    route: str,
    dev_id: str,
    live: dict[str, Any],
    rel: dict[str, Any],
    export_product: str | None = None,
) -> dict[str, Any]:
    """The body for `route`, built from the current cloud state.

    A no-op unless `export_product` is given, which replaces only the export
    tariff code."""
    binding = _binding_from_relation(dev_id, rel)
    if export_product:
        binding["productCodeOut"] = export_product
    if route == "device/switchMode":
        return payload_from_switch_mode_read(
            dev_id, live, overrides={"dynamicPriceBinding": binding}
        )
    if route == "dynamicPrice/saveBinding":
        # Found in the app's ApiService as saveBinding(RequestBody); the body is
        # most likely the binding block on its own.
        return binding
    return {
        "devId": str(dev_id),
        "dynamicChargePrice": float(live.get("dynamicChargePrice") or 0),
        "dynamicDischargePrice": float(live.get("dynamicDischargePrice") or 0),
        "backupPowerReserveSoc": str(live.get("backupPowerReserveSoc", "100")),
        "selfConsumptioinReserveSoc": str(live.get("selfConsumptioinReserveSoc", "20")),
        "dynamicPriceBinding": binding,
    }


def _redact_probe(value: Any, full: bool) -> Any:
    """Mask personal fields and shorten long lists in a probe response."""
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if _PROBE_REDACT.search(str(k)) and v not in (None, "")
                else _redact_probe(v, full))
            for k, v in value.items()
        }
    if isinstance(value, list):
        items = [_redact_probe(v, full) for v in value]
        if not full and len(items) > PROBE_MAX_LIST:
            return [*items[:PROBE_MAX_LIST], f"... {len(value) - PROBE_MAX_LIST} more"]
        return items
    return value


def _clamp_before_charge_window(plan: PredbatPlan, end_time: datetime) -> datetime:
    """Never let a forced export run into a planned charge window.

    The cheap-rate charge needs the cube back in TOU mode, and Dynamic mode's
    import side prices against Agile, not the user's tariff. Predbat does not
    normally overlap an export and a charge, but a stale or odd plan must not
    be able to keep the cube exporting through the charge start."""
    window = plan.charge_window
    if not plan.charge_enabled or window is None:
        return end_time
    now = dt_util.utcnow()
    start = dt_util.as_utc(window.start)
    if now < start < dt_util.as_utc(end_time):
        _LOGGER.warning(
            "forced export would overlap the charge window — ending it at %s",
            start.isoformat(),
        )
        return start
    return end_time


# All weekday/weekend/DST tier-list keys the shim might have written to.
_TIER_LIST_KEYS: tuple[str, ...] = (
    "peakTimeList", "midPeakTimeList", "offPeakTimeList",
    "peakTimeListNonWorkDay", "midPeakTimeListNonWorkDay", "offPeakTimeListNonWorkDay",
    "dayLightPeakTimeList", "dayLightMidPeakTimeList", "dayLightOffPeakTimeList",
    "dayLightPeakTimeListNonWorkDay", "dayLightMidPeakTimeListNonWorkDay",
    "dayLightOffPeakTimeListNonWorkDay",
)

# Slot wire format is "HH:MM_HH:MM_PRICE.PP" (see api.build_slot). A shim slot
# is recognised by its price segment matching one of the synthetic SHIM_PRICE_*
# values to 2dp. Documented user-facing constraint: don't manually configure
# slots with prices in the synthetic set below.
#
# v0.6.3 migrated the synthetic prices from 0.01 / 0.20 / 1.00 to the current
# 2.22 / 3.33 / 4.44 (above Agile's 100p cap, repeating-digit pattern). v0.6.3
# kept the legacy tokens in the strip set for one release so any leftover
# shim slots from pre-v0.6.3 Predbat runs would be cleaned on next read.
# Dropped in v0.7 — by now any in-flight overrides have long since rotated
# out and the 5-day operational window since v0.6.3 has stripped any cube
# memory residue via continuous shim operation.
_SHIM_PRICE_TOKENS: frozenset[str] = frozenset(
    f"{p:.2f}" for p in (SHIM_PRICE_OFF_PEAK, SHIM_PRICE_MID_PEAK, SHIM_PRICE_PEAK)
)


def _slot_is_shim_signature(slot: str) -> bool:
    """True if `slot`'s price segment matches a synthetic shim price."""
    parts = slot.rsplit("_", 1)
    if len(parts) != 2:
        return False
    return parts[1] in _SHIM_PRICE_TOKENS


def _strip_shim_slots(state: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Return (cleaned_state, n_stripped). Removes any tier-list slot whose
    price matches a SHIM_PRICE_* signature, across every weekday/weekend/DST
    variant. Original dict is not mutated."""
    cleaned = dict(state)
    stripped = 0
    for key in _TIER_LIST_KEYS:
        original = state.get(key)
        if not original:
            continue
        kept = [s for s in original if not _slot_is_shim_signature(str(s))]
        if len(kept) != len(original):
            stripped += len(original) - len(kept)
            cleaned[key] = kept
    return cleaned, stripped


# Non-DST tier-list keys — the six lists a user/external tool might paint
# manual TOU slots into. The DST variants (dayLight*) are deliberately
# excluded from the v1.0 repair-flow wipe target: the cube's DST tier
# lists are the genuinely shared state across the year. Wiping them is
# almost never what the user wants when their problem is a manual
# weekday schedule conflicting with Predbat.
_USER_TIER_LIST_KEYS: tuple[str, ...] = (
    "peakTimeList",
    "midPeakTimeList",
    "offPeakTimeList",
    "peakTimeListNonWorkDay",
    "midPeakTimeListNonWorkDay",
    "offPeakTimeListNonWorkDay",
)


def _is_shim_dynamic_signature(state: dict[str, Any]) -> bool:
    """True if `state` (a getSwitchMode read) is in Dynamic-tariff mode with
    exactly the thresholds a shim forced export writes.

    That combination is the shim's own fingerprint (the same idea as the
    synthetic TOU slot prices): a cube found like this at snapshot time is a
    leftover from an export window whose revert never ran (for example HA
    restarted mid-window), not a baseline the user chose. A user's own
    Dynamic-tariff thresholds will not normally match, and are left alone."""
    try:
        if int(state.get("touType") or 0) != TOU_TYPE_DYNAMIC:
            return False
        sell = float(state.get("dynamicDischargePrice"))
        buy = float(state.get("dynamicChargePrice"))
    except (TypeError, ValueError):
        return False
    # The sell-above is 0 now; earlier builds wrote up to 5p, so anything in
    # 0..5 with buy-below 0 is a shim export. The hold setting (sell 999.99) is not.
    export = (
        0.0 <= sell <= float(DYNAMIC_EXPORT_LEGACY_SELL_ABOVE_MAX_P)
        and buy == float(DYNAMIC_EXPORT_BUY_BELOW_P)
    )
    return export or _is_shim_dynamic_charge_signature(state)


def _is_shim_dynamic_charge_signature(state: dict[str, Any]) -> bool:
    """True if `state` is Dynamic-tariff mode with the thresholds a shim forced
    charge writes (buy-below and sell-above both at the maximum)."""
    try:
        if int(state.get("touType") or 0) != TOU_TYPE_DYNAMIC:
            return False
        sell = float(state.get("dynamicDischargePrice"))
        buy = float(state.get("dynamicChargePrice"))
    except (TypeError, ValueError):
        return False
    return sell == float(DYNAMIC_CHARGE_SELL_ABOVE_P) and buy == float(DYNAMIC_CHARGE_BUY_BELOW_P)


def _has_leftover_charge_thresholds(state: dict[str, Any]) -> bool:
    """True if `state` is NOT in Dynamic mode but still carries the thresholds a
    shim forced charge wrote (buy-below and sell-above both 999.99).

    The cloud keeps the last Dynamic thresholds and Charging Limit after the
    cube goes back to Self-Consumption / TOU, so a charge whose revert could not
    reset them (an older build, or a failed hold step) leaves its Charging Limit
    behind. That limit is the charge target, not the user's setting."""
    try:
        if int(state.get("touType") or 0) == TOU_TYPE_DYNAMIC:
            return False
        sell = float(state.get("dynamicDischargePrice"))
        buy = float(state.get("dynamicChargePrice"))
    except (TypeError, ValueError):
        return False
    return sell == float(DYNAMIC_CHARGE_SELL_ABOVE_P) and buy == float(DYNAMIC_CHARGE_BUY_BELOW_P)


def _is_user_dynamic_baseline(state: dict[str, Any]) -> bool:
    """True if `state` is the user's own Dynamic-tariff mode (TOU work status,
    `touType` 1). A stale shim export is cleaned to `touType` 0 at snapshot
    time, so a baseline that still says 1 is the user's."""
    try:
        return (
            str(state.get("workStatus")) == WORK_STATUS_TOU
            and int(state.get("touType") or 0) == TOU_TYPE_DYNAMIC
        )
    except (TypeError, ValueError):
        return False


def _is_empty_tou(state: dict[str, Any]) -> bool:
    """True if `state` is TOU mode with no slots in any tier list."""
    if str(state.get("workStatus")) != WORK_STATUS_TOU:
        return False
    return not any(state.get(key) for key in _TIER_LIST_KEYS)


def _recover_from_stale_dynamic(state: dict[str, Any]) -> dict[str, Any]:
    """The baseline to use when the cube is found in a shim Dynamic override whose
    revert never ran (HA restarted mid-window): Dynamic switched off, and the mode
    set to Self-Consumption.

    The override left the cube in TOU (`workStatus` 2), which is a side effect of
    Dynamic mode, not the user's setting; adopting it made later reverts put the cube
    back in ToU (seen 2026-10-15). The shim's default mode is Self-Consumption, and a
    ToU baseline with no slots behaves the same way anyway."""
    recovered = _clear_dynamic(state)
    recovered["workStatus"] = WORK_STATUS_SELF_CONSUMPTION
    return recovered


def _clear_dynamic(state: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `state` with Dynamic-tariff mode switched off:
    standard TOU `touType` and no dynamic thresholds."""
    cleaned = dict(state)
    cleaned["touType"] = 0
    cleaned.pop("dynamicChargePrice", None)
    cleaned.pop("dynamicDischargePrice", None)
    return cleaned


def _first_non_shim_price(slots: list[Any] | None) -> float | None:
    """Return the price of the first non-shim slot in `slots`, or None if none.

    Used by the repair-flow detection in `repairs.py` to determine whether
    a tier list contains any user-painted (non-shim) slots that would
    conflict with Predbat's control of the cube."""
    for raw in slots or []:
        parts = str(raw).rsplit("_", 1)
        if len(parts) != 2:
            continue
        price_token = parts[1]
        if price_token in _SHIM_PRICE_TOKENS:
            continue
        try:
            return float(price_token)
        except ValueError:
            continue
    return None


def has_non_shim_user_slots(state: dict[str, Any]) -> bool:
    """True if any of the non-DST tier lists contains a non-shim slot.

    The v1.0 repair flow uses this against `coordinator.switch_mode` to
    decide whether to raise the 'Predbat priority' issue. Shim-signature
    slots are ignored — they're Predbat's own in-flight overrides and
    don't conflict with anything."""
    for key in _USER_TIER_LIST_KEYS:
        if _first_non_shim_price(state.get(key)) is not None:
            return True
    return False


class PredbatShim:
    """Per-device Predbat shim. One instance per EP Cube config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        client: EPCubeClient,
        force_export_mode: str = DEFAULT_FORCE_EXPORT_MODE,
        price_coordinator: Any = None,
        force_charge_mode: str = DEFAULT_FORCE_CHARGE_MODE,
        status_coordinator: Any = None,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        # Optional EPCubePriceCoordinator: its half-hourly export prices let a
        # forced export pick a sell-above that is safely under them.
        self.price_coordinator = price_coordinator
        # How charge windows are realised: a TOU off-peak slot (default) or
        # Dynamic-tariff mode with the Charging Limit SoC as the target.
        self.force_charge_mode = force_charge_mode
        # Optional main coordinator (DeviceStatus): lets a Dynamic charge check that
        # the battery really is charging from the grid a few minutes in.
        self.status_coordinator = status_coordinator
        self._charge_check_unsub = None
        # How discharge_start realises "force export": legacy TOU peak slot
        # (default) or Dynamic-tariff mode (real grid export).
        self.force_export_mode = force_export_mode
        # Baseline = full getSwitchMode payload snapshotted at first override.
        self._baseline: dict[str, Any] | None = None
        self._active_override: dict[str, Any] | None = None
        self._revert_unsub = None
        # Dynamic-export safety net: a second timer a few minutes after the
        # window end that verifies the cube really left Dynamic mode.
        self._watchdog_unsub = None
        self._retry_unsub = None
        self._revert_retries = 0

    @property
    def is_active(self) -> bool:
        return self._active_override is not None

    # ------------------------------------------------------------------
    # Public service handlers
    # ------------------------------------------------------------------
    async def charge_start(self, end_time: datetime, target_soc_pct: float) -> None:
        """Force grid charge until end_time. target_soc_pct is informational —
        the cloud's TOU model has no per-slot SoC target; the inverter uses
        the tier prices to optimise.

        With `force_charge_mode == "dynamic"` the window is instead realised by
        Dynamic-tariff mode (see `_charge_start_dynamic`), where target_soc_pct
        becomes the Charging Limit SoC and the charge stops there."""
        if self.force_charge_mode == FORCE_CHARGE_MODE_DYNAMIC:
            await self._charge_start_dynamic(end_time, target_soc_pct)
            return
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
        see ARCHITECTURE.md.

        With `force_export_mode == "dynamic"` the window is instead realised
        by Dynamic-tariff mode (see `_discharge_start_dynamic`), which does
        export battery energy to the grid."""
        if self.force_export_mode == FORCE_EXPORT_MODE_DYNAMIC:
            await self._discharge_start_dynamic(end_time, target_soc_pct)
            return
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

    # ------------------------------------------------------------------
    # Dynamic-tariff forced export
    # ------------------------------------------------------------------
    async def _discharge_start_dynamic(
        self,
        end_time: datetime,
        target_soc_pct: float,
        sell_above_override: float | None = None,
    ) -> None:
        """Force a grid export by switching the cube to Dynamic-tariff mode.

        The cube sells while its export price is above `dynamicDischargePrice`
        and stops at the Reserve SoC (`selfConsumptioinReserveSoc`), so a low
        sell-above makes it export at full power and Predbat's target SoC
        becomes the stopping point. Buy-below is set to 0 so it never imports
        for the export. The window ends with a revert to the baseline and a
        watchdog that checks the cube really left Dynamic mode, because a cube
        stuck in Dynamic mode would not take the cheap-rate charge.
        """
        await self._snapshot_baseline()
        reserve = self._dynamic_reserve(target_soc_pct)
        params = {
            "kind": KIND_DISCHARGE,
            "dynamic": True,
            "end_time": end_time,
            "reserve_pct": reserve,
        }
        if self._matches_active(params):
            _LOGGER.debug("discharge_start (dynamic): idempotent no-op")
            return
        await self._warn_if_cube_has_no_price("export", "export")
        sell_above = (
            sell_above_override
            if sell_above_override is not None
            else self._sell_above_for_window(end_time)
        )
        payload = self._build_dynamic_override(reserve, sell_above)
        await self._apply_dynamic_override(payload, params)
        self._schedule_revert(end_time)
        self._schedule_watchdog(end_time)
        _LOGGER.info(
            "discharge_start applied: dynamic export until %s, reserve %d%% "
            "(sell above %.2fp, buy below %.2fp)",
            end_time.isoformat(), reserve,
            sell_above, DYNAMIC_EXPORT_BUY_BELOW_P,
        )

    async def debug_dynamic_export(
        self, duration_minutes: int, reserve_pct: float, sell_above: float | None = None
    ) -> None:
        """Manual forced export for `duration_minutes`, stopping at `reserve_pct`.
        Diagnostic tool (like debug_freeze) for exercising the Dynamic-tariff
        path against the live cube without involving Predbat."""
        end_time = dt_util.utcnow() + timedelta(minutes=duration_minutes)
        await self._discharge_start_dynamic(end_time, reserve_pct, sell_above)

    async def debug_dynamic_charge(self, duration_minutes: int, limit_pct: float) -> None:
        """Experiment: charge from the grid in Dynamic-tariff mode for
        `duration_minutes`, stopping at the Charging Limit SoC `limit_pct`.

        buy-below at the maximum makes the cube always buy; sell-above at the
        maximum stops it exporting. Used to learn how the cube behaves on the
        buy side (charge rate, how precisely the limit stops it) before any
        Predbat charge window is allowed to use it. Reverts like an export."""
        end_time = dt_util.utcnow() + timedelta(minutes=duration_minutes)
        await self._charge_start_dynamic(end_time, limit_pct)

    async def _charge_start_dynamic(self, end_time: datetime, limit_pct: float) -> None:
        await self._snapshot_baseline()
        limit = max(0, min(100, int(math.ceil(float(limit_pct)))))
        params = {
            "kind": KIND_CHARGE,
            "dynamic": True,
            "end_time": end_time,
            "limit_pct": limit,
        }
        if self._matches_active(params):
            _LOGGER.debug("charge_start (dynamic): idempotent no-op")
            return
        await self._warn_if_cube_has_no_price("import", "charge")
        payload = payload_from_switch_mode_read(
            self.client.dev_id,
            self._baseline or {},
            overrides={
                "workStatus": WORK_STATUS_TOU,
                "touType": TOU_TYPE_DYNAMIC,
                "dynamicChargePrice": float(DYNAMIC_CHARGE_BUY_BELOW_P),
                "dynamicDischargePrice": float(DYNAMIC_CHARGE_SELL_ABOVE_P),
                "backupPowerReserveSoc": str(limit),
                "allowChargingXiaGrid": "1",
            },
        )
        await self._apply_dynamic_override(
            payload, params, extra_verify=("dynamicChargePrice", "backupPowerReserveSoc")
        )
        self._schedule_revert(end_time)
        self._schedule_watchdog(end_time)
        self._schedule_charge_check(end_time)
        _LOGGER.info(
            "charge_start applied: dynamic charge until %s, limit %d%% "
            "(buy below %.2fp, sell above %.2fp)",
            end_time.isoformat(), limit,
            DYNAMIC_CHARGE_BUY_BELOW_P, DYNAMIC_CHARGE_SELL_ABOVE_P,
        )

    # ------------------------------------------------------------------
    # Dynamic charge safeguards
    # ------------------------------------------------------------------
    async def _warn_if_cube_has_no_price(self, side: str, what: str) -> None:
        """Before a Dynamic window: does the cube have a price for this half-hour?

        With no valid price data the cube runs in Self-Consumption (the app's own
        wording), so a Dynamic charge or export would silently not happen. The
        price coordinator reads the same data the cube uses; if it has nothing for
        the current slot, say so. The write is still made (it does no harm); this
        only makes the miss visible.

        The coordinator only polls while a price sensor is enabled (they are off by
        default), so its data can be hours old: refresh it first, and judge nothing
        if the refresh fails."""
        coordinator = self.price_coordinator
        if coordinator is None:
            return          # no price source: nothing to judge by
        try:
            await coordinator.async_refresh()
        except Exception as err:  # noqa: BLE001 - a failed look-up must never block the write
            _LOGGER.debug("price refresh before a Dynamic %s failed: %s", what, err)
            return
        data = getattr(coordinator, "data", None)
        if data is None or not getattr(coordinator, "last_update_success", True):
            return          # could not fetch: nothing to judge by
        notification_id = f"ep_cube_{self.entry_id}_no_price"
        now = dt_util.utcnow()
        if any(s["start"] <= now < s["end"] for s in data.get(side, [])):
            persistent_notification.async_dismiss(self.hass, notification_id)
            return
        message = (
            f"The cube has no {side} price for the current half-hour, so a Dynamic {what} "
            "will not happen: with no valid tariff data the cube runs in Self-Consumption. "
            "Check the Dynamic tariff in the EP Cube app."
        )
        _LOGGER.warning("dynamic %s: %s", what, message)
        persistent_notification.async_create(
            self.hass, message, title="EP Cube: no price data",
            notification_id=notification_id,
        )

    def _schedule_charge_check(self, end_time: datetime) -> None:
        """Arm the check that a Dynamic charge really started."""
        self._cancel_charge_check()
        when = dt_util.utcnow() + timedelta(minutes=DYNAMIC_CHARGE_CHECK_MINUTES)
        if when >= dt_util.as_utc(end_time):
            return          # the window is shorter than the check: nothing to verify
        self._charge_check_unsub = async_track_point_in_utc_time(
            self.hass, self._on_charge_check, when
        )

    def _cancel_charge_check(self) -> None:
        if self._charge_check_unsub is not None:
            self._charge_check_unsub()
            self._charge_check_unsub = None

    async def _on_charge_check(self, _now: datetime) -> None:
        """A few minutes into a Dynamic charge: is the battery charging from the grid?

        If not (the cube did not take the setting, or it has no price data), say so
        and re-send the write once. Predbat re-plans from the real SoC every
        cycle, so a miss costs one cheap slot, never a stuck state."""
        self._cancel_charge_check()
        override = self._active_override
        if not override or override.get("kind") != KIND_CHARGE or not override.get("dynamic"):
            return
        status = getattr(self.status_coordinator, "data", None)
        if status is None:
            return
        if status.soc_pct >= float(override.get("limit_pct", 100)) - 1:
            return          # at (or within a point of) the target: done
        if status.grid_power_w > 500 and status.battery_power_w > 500:
            return          # importing and charging: working
        message = (
            f"A Dynamic charge to {override.get('limit_pct')}% started {DYNAMIC_CHARGE_CHECK_MINUTES} "
            f"minutes ago but the battery is not charging from the grid (SoC {status.soc_pct:.0f}%, "
            f"grid {status.grid_power_w:.0f} W, battery {status.battery_power_w:.0f} W). "
            "Re-sending the setting once."
        )
        _LOGGER.warning("dynamic charge: %s", message)
        persistent_notification.async_create(
            self.hass, message, title="EP Cube: charge did not start",
            notification_id=f"ep_cube_{self.entry_id}_charge_check",
        )
        try:
            await self.client.switch_mode(
                override["applied_schedule"],
                verify_keys=("workStatus", "touType", "dynamicChargePrice", "backupPowerReserveSoc"),
            )
        except EPCubeError as err:
            _LOGGER.error("dynamic charge: re-sending the setting failed (%s)", err)

    def _dynamic_reserve(self, target_soc_pct: float) -> int:
        """Reserve SoC for a forced export: Predbat's target, rounded UP to a whole
        percent (the cube takes integers, and rounding up means the export never
        overshoots the target), never below the cube's own self-consumption
        reserve and never above 100. A target of 55 is written as 55."""
        floor = int(float((self._baseline or {}).get("selfConsumptioinReserveSoc") or 20))
        target = max(0.0, min(100.0, float(target_soc_pct)))
        return max(floor, min(100, int(math.ceil(target))))

    def _sell_above_for_window(self, end_time: datetime) -> float:
        """Sell-above threshold for a forced export ending at `end_time`.

        Always the default, 0.00: the cube exports at any positive export price,
        so the threshold needs no adjusting to the live tariff. What can still go
        wrong is a slot whose export price is zero or negative; the cube will not
        export then, so the window gets a warning (using the cube's own export
        prices from the price coordinator, when it has them).
        """
        slots = ((getattr(self.price_coordinator, "data", None) or {}).get("export")) or []
        now = dt_util.utcnow()
        prices = [s["price"] for s in slots if s["end"] > now and s["start"] < end_time]
        if prices and min(prices) <= float(DYNAMIC_EXPORT_SELL_ABOVE_P):
            _LOGGER.warning(
                "dynamic export: the export price falls to %.2fp in this window; "
                "the cube will not export in that slot",
                min(prices),
            )
        return float(DYNAMIC_EXPORT_SELL_ABOVE_P)

    def _build_dynamic_override(
        self, reserve_pct: int, sell_above: float = DYNAMIC_EXPORT_SELL_ABOVE_P
    ) -> dict[str, Any]:
        """Compose the /api/device/switchMode body that puts the cube into
        Dynamic-tariff mode with the forced-export thresholds."""
        return payload_from_switch_mode_read(
            self.client.dev_id,
            self._baseline or {},
            overrides={
                "workStatus": WORK_STATUS_TOU,
                "touType": TOU_TYPE_DYNAMIC,
                "dynamicChargePrice": float(DYNAMIC_EXPORT_BUY_BELOW_P),
                "dynamicDischargePrice": float(sell_above),
                "selfConsumptioinReserveSoc": str(reserve_pct),
                "allowChargingXiaGrid": "0",
            },
        )

    async def _apply_dynamic_override(
        self,
        payload: dict[str, Any],
        params: dict[str, Any],
        extra_verify: tuple[str, ...] = (),
    ) -> None:
        # Record the override BEFORE writing: if the write lands but its
        # read-back verification fails, the cube may really be in Dynamic mode
        # and the revert machinery must still know about it.
        self._active_override = {
            **params,
            "applied_mode": OPERATING_MODE_TOU,
            "applied_schedule": payload,
        }
        verify_keys = (
            "workStatus", "touType", "dynamicDischargePrice", "selfConsumptioinReserveSoc",
            *extra_verify,
        )
        try:
            await self.client.switch_mode(payload, verify_keys=verify_keys)
        except EPCubeError as err:
            _LOGGER.error(
                "dynamic export override failed (%s) — reverting to baseline", err
            )
            try:
                await self._revert_to_baseline()
            except EPCubeError:
                self._schedule_revert_retry()
            raise

    async def charge_freeze(self, end_time: datetime) -> None:
        """Hold the battery at its current charge until end_time: no discharge,
        while surplus solar can still charge it (Predbat's "freeze charge").

        Done by raising the Self-Consumption reserve to the current SoC, so the
        cube cannot discharge below it and the grid covers the house. Verified on
        the cube 2026-10-15: the battery stopped discharging within about three
        minutes with the reserve at the SoC, and solar still charged it. No ToU
        slot is involved. The reserve is fixed when the freeze starts (a repeated
        call does not ratchet it up as solar charges the battery), and the
        auto-revert restores the baseline reserve at end_time.

        Falls back to the legacy ToU mid-peak slot when the current SoC is not
        known or the baseline is not Self-Consumption (the reserve of another
        mode is not the same control)."""
        params = {"kind": KIND_FREEZE, "end_time": end_time}
        if self._matches_active(params):
            _LOGGER.debug("charge_freeze: idempotent no-op")
            return
        await self._snapshot_baseline()
        soc = self._current_soc_pct()
        baseline = self._baseline or {}
        if soc is None or str(baseline.get("workStatus")) != WORK_STATUS_SELF_CONSUMPTION:
            _LOGGER.info(
                "charge_freeze: %s, using the ToU mid-peak slot instead of a reserve hold",
                "no SoC reading" if soc is None else "the baseline is not Self-Consumption",
            )
            await self._freeze_with_tou_slot(end_time)
            return
        floor = int(float(baseline.get("selfConsumptioinReserveSoc") or 20))
        # Rounded DOWN, never above the SoC: a reserve above the SoC could be read as
        # a target to charge to.
        reserve = max(floor, min(100, int(math.floor(soc))))
        payload = payload_from_switch_mode_read(
            self.client.dev_id,
            baseline,
            overrides={"selfConsumptioinReserveSoc": str(reserve)},
        )
        # Record before writing: if the write lands but its verification fails the
        # reserve really is raised and the revert machinery must know.
        self._active_override = {
            **params,
            "reserve_pct": reserve,
            "applied_mode": OPERATING_MODE_SELF_CONSUMPTION,
            "applied_schedule": payload,
        }
        try:
            await self.client.switch_mode(
                payload, verify_keys=("workStatus", "selfConsumptioinReserveSoc")
            )
        except EPCubeError as err:
            _LOGGER.error("charge_freeze (reserve hold) failed (%s) - reverting", err)
            try:
                await self._revert_to_baseline()
            except EPCubeError:
                self._schedule_revert_retry()
            raise
        self._schedule_revert(end_time)
        _LOGGER.info(
            "charge_freeze applied: reserve held at %d%% until %s", reserve, end_time.isoformat()
        )

    def _current_soc_pct(self) -> float | None:
        """The battery's SoC from the status coordinator, or None if not available."""
        data = getattr(self.status_coordinator, "data", None)
        try:
            return float(data.soc_pct) if data is not None else None
        except (TypeError, ValueError, AttributeError):
            return None

    async def _freeze_with_tou_slot(self, end_time: datetime) -> None:
        """The legacy freeze: a ToU mid-peak slot, which the vendor describes as
        "not charging from grid, not supporting loads". Solar can still charge the
        battery in this slot (observed on a real cube, 2026-10-20), so it does not
        give a freeze export. Kept as the fallback and for discharge_freeze."""
        params = {"kind": KIND_FREEZE, "end_time": end_time}
        if self._matches_active(params):
            _LOGGER.debug("freeze (ToU slot): idempotent no-op")
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
        _LOGGER.info("freeze applied: mid-peak slot until %s", end_time.isoformat())

    async def discharge_freeze(self, end_time: datetime) -> None:
        """Predbat's "freeze export" (the battery must not absorb surplus solar, which
        is exported instead) is a different behaviour from freeze charge, and a
        reserve hold does not do it (solar would still charge the battery). Until that
        is tested on the cube it keeps the legacy ToU mid-peak slot."""
        await self._freeze_with_tou_slot(end_time)

    async def idle(self) -> None:
        """Restore the baseline. Equivalent to '_stop' for any active override."""
        if self.is_active:
            await self._revert_to_baseline()
            _LOGGER.info("idle: reverted to baseline")
        else:
            _LOGGER.debug("idle: no active override (no-op)")

    def user_reserve_pct(self) -> float | None:
        """The reserve the user has chosen, while a shim override is active.

        A Dynamic export sets the cube's Reserve SoC to Predbat's export target (so
        the cube knows where to stop). That is not the user's reserve. Predbat reads
        the reserve entity as its battery floor, so showing it the export target made
        it plan a flat SoC for the rest of the night and cancel the export it had just
        asked for (seen 2026-10-15). While an override is active this returns the
        reserve captured in the baseline; otherwise None (use the live reserve).
        """
        if self._active_override is None or not self._baseline:
            return None
        key = (
            "backupPowerReserveSoc"
            if str(self._baseline.get("workStatus")) == "3"
            else "selfConsumptioinReserveSoc"
        )
        try:
            return float(self._baseline.get(key))
        except (TypeError, ValueError):
            return None

    def cancel_revert_timer(self) -> None:
        """Public cancel — used during integration teardown."""
        self._cancel_revert()
        self._cancel_watchdog()
        self._cancel_charge_check()

    def patch_baseline(self, field: str, value: Any) -> None:
        """Update a single field in the captured baseline.

        Used when the user manually changes a switchMode field via switch
        or number entities while a shim override is active — keeps the
        baseline in sync with the user's new ground truth so the auto-revert
        at end_time doesn't undo their change. No-op if no baseline is held
        (in which case the next override will snapshot fresh and pick up
        the new value naturally).
        """
        if self._baseline is not None:
            self._baseline[field] = value

    def abandon_override(self) -> None:
        """Drop any active override without writing to the cube.

        Used when a user takes manual control (e.g. via the operating-mode
        select entity) while a shim window is still scheduled to revert.
        Cancels the pending revert timer and clears override + baseline
        state so the next Predbat call re-snapshots from the user's new
        ground truth rather than overwriting it.
        """
        self._cancel_revert()
        self._cancel_watchdog()
        self._active_override = None
        self._baseline = None
        self._revert_retries = 0

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
        Lazy — runs once per shim lifetime, on first override.

        Strips stale shim-signature slots from every tier list before storing.
        The cube cloud silently ignores tier-list diffs on TOU→Self-Consumption
        reverts (discovered 2026-05-21 live-cube verification), so previous
        shim runs may have left orphaned slots on the cube. Stripping at
        snapshot time means both the override and revert payloads will exclude
        them — self-healing without growing the cloud-write budget."""
        if self._baseline is not None:
            return
        try:
            live = await self.client.get_switch_mode()
        except EPCubeError as err:
            _LOGGER.error("baseline snapshot failed: %s", err)
            raise
        if _is_shim_dynamic_signature(live):
            # The cube is still in a forced-export Dynamic mode we left behind
            # (revert never ran, e.g. HA restarted mid-window). Never adopt it
            # as the baseline — a revert would put the cube straight back.
            _LOGGER.warning(
                "baseline snapshot found a stale shim Dynamic-tariff export "
                "(sell above %sp, reserve %s%%); treating the baseline as "
                "Self-Consumption with the default %s%% self-consumption reserve",
                live.get("dynamicDischargePrice"),
                live.get("selfConsumptioinReserveSoc"),
                DEFAULT_SELF_CONSUMPTION_RESERVE_PCT,
            )
            charge_leftover = _is_shim_dynamic_charge_signature(live)
            live = _recover_from_stale_dynamic(live)
            if charge_leftover:
                # A forced charge left its target in the Charging Limit SoC.
                live["backupPowerReserveSoc"] = str(DEFAULT_CHARGING_LIMIT_PCT)
            else:
                # The reserve the export left behind is Predbat's target, not the
                # user's setting - never restore it as the baseline.
                live["selfConsumptioinReserveSoc"] = str(DEFAULT_SELF_CONSUMPTION_RESERVE_PCT)
        elif _has_leftover_charge_thresholds(live):
            _LOGGER.warning(
                "baseline snapshot found the thresholds of a forced charge left in the "
                "cloud (Charging Limit %s%%); using the default %s%% instead",
                live.get("backupPowerReserveSoc"),
                DEFAULT_CHARGING_LIMIT_PCT,
            )
            live = dict(live)
            live["backupPowerReserveSoc"] = str(DEFAULT_CHARGING_LIMIT_PCT)
        self._baseline, stripped = _strip_shim_slots(live)
        if (
            stripped
            and _is_empty_tou(self._baseline)
            and not _is_user_dynamic_baseline(self._baseline)
        ):
            # The cube is in ToU and the only slots were the shim's own: a freeze (or
            # other slot override) whose revert never ran, e.g. HA restarted mid-window
            # (seen 2026-10-18). A user's own ToU mode has slots of its own, so what is
            # left is a side effect of the override, not a mode the user chose. Adopting
            # it made every later revert put the cube back in ToU.
            _LOGGER.warning(
                "baseline snapshot found only shim slots in ToU mode (an override "
                "left behind, e.g. by an HA restart); treating the baseline as "
                "Self-Consumption"
            )
            self._baseline = {**self._baseline, "workStatus": WORK_STATUS_SELF_CONSUMPTION}
        if stripped:
            _LOGGER.info(
                "baseline snapshotted: workStatus=%s self_reserve=%s backup_reserve=%s "
                "(stripped %d stale shim-signature slot(s))",
                self._baseline.get("workStatus"),
                self._baseline.get("selfConsumptioinReserveSoc"),
                self._baseline.get("backupPowerReserveSoc"),
                stripped,
            )
        else:
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
        """Compose a /api/device/switchMode payload that mirrors the baseline
        snapshot and appends an override slot to the appropriate tier list.

        The override slot covers now → end_time on the current day's profile
        (weekday or weekend). DST tier lists are preserved from the baseline
        untouched — overrides only fire when the cube is in non-DST mode,
        which is a pre-existing limitation of the shim.

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

        # Pick the right baseline tier key for this override.
        tier_key = {
            ("off_peak", False): "offPeakTimeList",
            ("mid_peak", False): "midPeakTimeList",
            ("peak", False): "peakTimeList",
            ("off_peak", True): "offPeakTimeListNonWorkDay",
            ("mid_peak", True): "midPeakTimeListNonWorkDay",
            ("peak", True): "peakTimeListNonWorkDay",
        }[(tier, is_weekend)]
        appended_tier = [*(baseline.get(tier_key) or []), override_slot]

        # Start from the full baseline snapshot, then apply our diffs:
        #   - flip workStatus → TOU (2)
        #   - force touType → standard TOU (0). A baseline read while the cube
        #     is in Dynamic-tariff mode carries touType 1, and mirroring that
        #     into a slot-based TOU write is a schedule conflict the cloud
        #     rejects with HTTP 500 "Failure".
        #   - replace the target tier list with our appended version
        #   - honour the caller's allow_grid_charge intent
        payload = payload_from_switch_mode_read(
            self.client.dev_id,
            baseline,
            overrides={
                "workStatus": WORK_STATUS_TOU,
                "touType": TOU_TYPE_STANDARD,
                tier_key: appended_tier,
                "allowChargingXiaGrid": "1" if allow_grid_charge else "0",
            },
        )
        # Slot-based TOU has no use for Dynamic-tariff thresholds.
        payload.pop("dynamicChargePrice", None)
        payload.pop("dynamicDischargePrice", None)
        return payload

    async def _apply_tou_override(
        self, payload: dict[str, Any], params: dict[str, Any]
    ) -> None:
        """Single POST: /api/device/switchMode bundles mode-switch with schedule-save.
        Verifies the cube adopted both workStatus and the affected tier list."""
        # Verify the tier list that we appended into — figure out which one
        # from the payload diff against the baseline.
        verify_keys: tuple[str, ...] = ("workStatus",)
        if self._baseline is not None:
            for key in (
                "peakTimeList", "midPeakTimeList", "offPeakTimeList",
                "peakTimeListNonWorkDay", "midPeakTimeListNonWorkDay", "offPeakTimeListNonWorkDay",
            ):
                if (payload.get(key) or []) != (self._baseline.get(key) or []):
                    verify_keys = ("workStatus", key)
                    break
        try:
            await self.client.switch_mode(payload, verify_keys=verify_keys)
        except EPCubeError as err:
            _LOGGER.error("switchMode override failed: %s", err)
            raise
        self._active_override = {
            **params,
            "applied_mode": OPERATING_MODE_TOU,
            "applied_schedule": payload,
        }

    async def _revert_to_baseline(self) -> None:
        """Restore the snapshotted baseline.

        Single-write revert: POST `switchMode` with the captured baseline
        payload (mode + reserves + the clean tier-list from snapshot time).

        - If baseline workStatus is TOU (`"2"`), no mode transition — cube
          applies both the mode value and the tier-list diff in one go.
        - If baseline workStatus is non-TOU (`"1"`/`"3"`), the cube switches
          mode but silently drops the tier-list write (TOU→non-TOU transition
          quirk verified 2026-05-21). The override's shim-signature slots
          (£0.01 / £0.20 / £1.00 prices) stay in the cube's TOU memory until
          the next override fires — `_snapshot_baseline()` strips them via
          `_strip_shim_slots()` on capture, so they never compound and never
          leak into a future baseline.

        Task #4 (2026-05-22) ruled out a single-call clean-revert path:
          - Web portal HAR showed the "Clear" button is purely client-side.
          - Web portal has per-mode endpoints (`/v1/api/home/setSelfConsumption`
            etc.) with minimal payloads, but probing `/api/device/setSelfConsumption`
            on the mobile-app surface returned 404 — the per-mode family is
            web-portal-only.

        Cosmetic cost: a user who opens the EP Cube app between override
        sessions will see shim-signature TOU slots in the cube's stored
        schedule (cleaned on the next Predbat activation). Functionally,
        the cube is in the correct mode and behaves correctly.
        """
        self._cancel_revert()
        self._cancel_charge_check()
        if self._baseline is None:
            _LOGGER.warning("_revert_to_baseline: no baseline available — leaving cloud as-is")
            self._active_override = None
            return

        was_dynamic = bool(self._active_override and self._active_override.get("dynamic"))
        try:
            await self._post_baseline(self._baseline, verify_tou_type=was_dynamic)
        except EPCubeError as err:
            _LOGGER.error(
                "revert FAILED — battery may be stuck on override. err=%s",
                err,
            )
            raise
        self._active_override = None
        self._revert_retries = 0

    async def _post_user_dynamic_baseline(self, base: dict[str, Any]) -> None:
        """Restore a baseline that was the user's OWN Dynamic-tariff mode.

        A write that says `touType` 1 while changing the TOU slot lists is the
        shape the cloud rejects with HTTP 500 "Failure" (the issue #1 report),
        and by the time of the revert the cube holds the override's slots, so
        restoring the baseline lists *and* `touType` 1 in one write would be
        exactly that. Two writes instead:

        1. the baseline TOU lists with `touType` 0 (an ordinary TOU edit);
        2. `touType` 1 with the saved thresholds and the now-unchanged lists.

        If step 2 fails the cube is left in plain TOU with the user's lists,
        which is safe; the caller's retry machinery re-runs both steps."""
        tou_step = payload_from_switch_mode_read(
            self.client.dev_id, base, overrides={"touType": TOU_TYPE_STANDARD}
        )
        tou_step.pop("dynamicChargePrice", None)
        tou_step.pop("dynamicDischargePrice", None)
        await self.client.switch_mode(tou_step, verify_keys=("workStatus", "touType"))
        dynamic_step = payload_from_switch_mode_read(self.client.dev_id, base)
        await self.client.switch_mode(
            dynamic_step,
            verify_keys=("workStatus", "touType", "dynamicDischargePrice"),
        )

    async def _post_baseline(self, base: dict[str, Any], *, verify_tou_type: bool) -> None:
        """Write `base` back to the cube as a switchMode payload.

        After a Dynamic-tariff export the read-back must also show the cube
        has left Dynamic mode (`touType`). If the baseline is a TOU mode with
        no slots at all and the cloud rejects switching Dynamic → that empty
        TOU (the app and the cloud both want real off-peak/peak slots), fall
        back to Self-Consumption, which is how the cube behaves with an
        empty TOU schedule anyway.
        """
        if _is_user_dynamic_baseline(base):
            await self._post_user_dynamic_baseline(base)
            return
        if verify_tou_type:
            await self._leave_safe_dynamic_thresholds(base)
        payload = payload_from_switch_mode_read(self.client.dev_id, base)
        verify_keys = ("workStatus", "touType") if verify_tou_type else ("workStatus",)
        try:
            await self.client.switch_mode(payload, verify_keys=verify_keys)
        except EPCubeError as err:
            if not (verify_tou_type and _is_empty_tou(base)):
                raise
            _LOGGER.warning(
                "revert to an empty TOU schedule was rejected (%s) — "
                "retrying as Self-Consumption (equivalent behaviour)",
                err,
            )
            fallback = payload_from_switch_mode_read(
                self.client.dev_id,
                base,
                overrides={
                    "workStatus": WORK_STATUS_SELF_CONSUMPTION,
                    "touType": TOU_TYPE_STANDARD,
                },
            )
            await self.client.switch_mode(fallback, verify_keys=("workStatus",))

    async def _leave_safe_dynamic_thresholds(self, base: dict[str, Any]) -> None:
        """First step of leaving a shim Dynamic override: re-write Dynamic mode
        with the safe hold thresholds (buy 0, sell 999.99) and the baseline's
        reserve and Charging Limit.

        The cloud keeps the last Dynamic thresholds and Charging Limit after the
        cube goes back to Self-Consumption / TOU (verified 2026-10-15: a forced
        charge left buy/sell at 999.99 and the Charging Limit at the test's 89%),
        and a plain baseline write does not reset them. Writing them while the
        cube is still in Dynamic mode does. Best effort: leaving Dynamic is what
        matters, so a failure here is logged and the baseline write still runs.
        """
        hold = payload_from_switch_mode_read(
            self.client.dev_id,
            base,
            overrides={
                "workStatus": WORK_STATUS_TOU,
                "touType": TOU_TYPE_DYNAMIC,
                "dynamicChargePrice": float(DYNAMIC_HOLD_BUY_BELOW_P),
                "dynamicDischargePrice": float(DYNAMIC_HOLD_SELL_ABOVE_P),
            },
        )
        try:
            await self.client.switch_mode(
                hold,
                verify_keys=(
                    "workStatus", "touType", "dynamicChargePrice", "dynamicDischargePrice",
                    "backupPowerReserveSoc", "selfConsumptioinReserveSoc",
                ),
            )
        except EPCubeError as err:
            _LOGGER.warning(
                "could not leave safe Dynamic thresholds before the revert (%s) - "
                "continuing with the baseline write", err,
            )

    def _schedule_revert_retry(self) -> None:
        """Retry a failed revert shortly, a bounded number of times. A cube
        left in Dynamic mode would miss the cheap-rate charge, so a transient
        cloud error must not be the end of the story."""
        if self._retry_unsub is not None:
            return
        if self._revert_retries >= DYNAMIC_REVERT_MAX_RETRIES:
            _LOGGER.error(
                "revert still failing after %d retries — giving up; check the cube mode",
                self._revert_retries,
            )
            return
        self._revert_retries += 1
        self._retry_unsub = async_call_later(
            self.hass, DYNAMIC_REVERT_RETRY_SECONDS, self._on_revert_retry
        )

    async def _on_revert_retry(self, _now: datetime) -> None:
        if self._retry_unsub is not None:
            self._retry_unsub()
            self._retry_unsub = None
        _LOGGER.info("retrying revert (attempt %d)", self._revert_retries)
        try:
            await self._revert_to_baseline()
        except EPCubeError:
            self._schedule_revert_retry()

    def _schedule_watchdog(self, end_time: datetime) -> None:
        """Arm the post-window check for a dynamic export."""
        self._cancel_watchdog()
        when = dt_util.as_utc(end_time) + timedelta(minutes=DYNAMIC_REVERT_WATCHDOG_MINUTES)
        self._watchdog_unsub = async_track_point_in_utc_time(
            self.hass, self._on_watchdog, when
        )

    def _cancel_watchdog(self) -> None:
        if self._watchdog_unsub is not None:
            self._watchdog_unsub()
            self._watchdog_unsub = None

    async def _on_watchdog(self, _now: datetime) -> None:
        """A few minutes after a dynamic export window: make sure the cube is
        really out of Dynamic mode, and force it out if it is not."""
        # Cancel (not just forget) the handle: harmless when this is the timer
        # firing, and it stops a stray timer when called directly.
        self._cancel_watchdog()
        if self._active_override is not None and self._active_override.get("dynamic"):
            _LOGGER.warning(
                "dynamic export watchdog: override still active after the window — retrying revert"
            )
            try:
                await self._revert_to_baseline()
            except EPCubeError:
                self._schedule_revert_retry()
            return
        try:
            live = await self.client.get_switch_mode()
        except EPCubeError as err:
            _LOGGER.warning("dynamic export watchdog: cannot read the cube (%s)", err)
            return
        if not _is_shim_dynamic_signature(live):
            _LOGGER.debug("dynamic export watchdog: cube is out of Dynamic mode — all good")
            return
        _LOGGER.error(
            "dynamic export watchdog: cube is STILL in forced-export Dynamic mode — forcing it back"
        )
        base = (
            _clear_dynamic(self._baseline)
            if self._baseline is not None
            else _recover_from_stale_dynamic(live)
        )
        try:
            await self._post_baseline(base, verify_tou_type=True)
        except EPCubeError as err:
            _LOGGER.error("dynamic export watchdog: forced revert FAILED (%s)", err)
            self._schedule_revert_retry()

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
        if self._retry_unsub is not None:
            self._retry_unsub()
            self._retry_unsub = None

    async def _on_revert_timer(self, _now: datetime) -> None:
        _LOGGER.info("revert timer fired — restoring baseline")
        if self._revert_unsub is not None:
            # Cancel (not just forget) the handle — see _on_watchdog.
            self._revert_unsub()
            self._revert_unsub = None
        try:
            await self._revert_to_baseline()
        except EPCubeError:
            self._schedule_revert_retry()


# ---------- service registration ----------


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the Predbat shim services on the HA service bus.

    Handlers read the planned window from `predbat.best_*` entities via
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
        # Where the plan lands at the end of the window (the slot's Limit % on the
        # plan page) is the target. predbat.best_export_limit is the limit Predbat
        # would program into an inverter, a few points lower (50 against 55 on
        # 2026-10-15), so it is only the floor: never export deeper than it.
        programmed = plan.discharge_target_soc_pct if plan.discharge_target_soc_pct is not None else 0
        planned = plan.discharge_planned_end_soc_pct
        target_soc_pct = float(max(programmed, planned) if planned is not None else programmed)
        end_time = plan.discharge_window.end
        if shim.force_export_mode == FORCE_EXPORT_MODE_DYNAMIC:
            end_time = _clamp_before_charge_window(plan, end_time)
        await shim.discharge_start(end_time=end_time, target_soc_pct=target_soc_pct)

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

    async def handle_debug_dynamic_export(call: ServiceCall) -> None:
        # Diagnostic: skips read_plan and forces a Dynamic-tariff export for
        # `duration_minutes`, stopping at `reserve_pct`.
        shim = _resolve_shim(call)
        duration = int(call.data.get("duration_minutes", 5))
        reserve = float(call.data.get("reserve_pct", 50))
        sell_above = call.data.get("sell_above")
        _LOGGER.warning(
            "debug_dynamic_export: bypassing predbat plan — forced export for %d min, reserve %.0f%%, sell above %s",
            duration, reserve, "guard" if sell_above is None else f"{sell_above:.2f}p",
        )
        await shim.debug_dynamic_export(duration, reserve, sell_above)

    async def handle_debug_dynamic_charge(call: ServiceCall) -> None:
        # Diagnostic: skips read_plan and charges from the grid in Dynamic-tariff
        # mode for `duration_minutes` (max 15), stopping at `limit_pct`.
        shim = _resolve_shim(call)
        duration = int(call.data.get("duration_minutes", 5))
        limit = float(call.data["limit_pct"])
        _LOGGER.warning(
            "debug_dynamic_charge: bypassing predbat plan — forced charge for %d min, limit %.0f%%",
            duration, limit,
        )
        await shim.debug_dynamic_charge(duration, limit)

    async def handle_debug_freeze(call: ServiceCall) -> None:
        # Diagnostic: skips read_plan and freezes for `duration_minutes`.
        shim = _resolve_shim(call)
        duration = int(call.data.get("duration_minutes", 5))
        end_time = dt_util.utcnow() + timedelta(minutes=duration)
        _LOGGER.warning(
            "debug_freeze: bypassing predbat plan — freezing for %d min (end=%s)",
            duration, end_time.isoformat(),
        )
        await shim.charge_freeze(end_time=end_time)

    async def handle_debug_probe(call: ServiceCall) -> dict[str, Any]:
        shim = _resolve_shim(call)
        route = call.data["route"]
        params = {"devId": shim.client.dev_id, **call.data["params"]}
        try:
            data = await shim.client.probe(route, params)
        except EPCubeError as err:
            _LOGGER.warning("debug_probe %s failed: %s", route, err)
            return {"ok": False, "route": route, "error": str(err)}
        result = _redact_probe(data, call.data["full"])
        _LOGGER.warning("debug_probe %s -> %s", route, result)
        return {"ok": True, "route": route, "data": result}

    async def handle_debug_probe_write(call: ServiceCall) -> dict[str, Any]:
        shim = _resolve_shim(call)
        client = shim.client
        route = call.data["route"]
        live = await client.get_switch_mode()
        rel = await client.probe("dynamicPrice/getRelation", {"devId": client.dev_id})
        body = build_probe_write_body(
            route, client.dev_id, live, rel, call.data.get("export_product")
        )
        _LOGGER.warning(
            "debug_probe_write POST %s (mode before: workStatus=%s touType=%s)",
            route, live.get("workStatus"), live.get("touType"),
        )
        try:
            data = await client.probe_post(route, body)
            result: dict[str, Any] = {
                "ok": True, "route": route, "data": _redact_probe(data, False)
            }
        except EPCubeError as err:
            _LOGGER.warning("debug_probe_write %s failed: %s", route, err)
            result = {"ok": False, "route": route, "error": str(err)}
        # The cloud can take a minute or two to settle, so this read-back is
        # only a first look; check getSwitchMode again later.
        after = await client.get_switch_mode()
        result["after"] = {k: after.get(k) for k in (
            "workStatus", "touType", "dynamicChargePrice", "dynamicDischargePrice",
            "selfConsumptioinReserveSoc", "backupPowerReserveSoc")}
        return result

    hass.services.async_register(DOMAIN, SERVICE_CHARGE_START, handle_charge_start, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_STOP, handle_charge_stop, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCHARGE_START, handle_discharge_start, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCHARGE_STOP, handle_discharge_stop, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_FREEZE, handle_charge_freeze, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCHARGE_FREEZE, handle_discharge_freeze, schema=SHIM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_IDLE, handle_idle, schema=SHIM_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_DEBUG_FREEZE, handle_debug_freeze, schema=DEBUG_FREEZE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DEBUG_DYNAMIC_CHARGE,
        handle_debug_dynamic_charge,
        schema=DEBUG_DYNAMIC_CHARGE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DEBUG_DYNAMIC_EXPORT,
        handle_debug_dynamic_export,
        schema=DEBUG_DYNAMIC_EXPORT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DEBUG_PROBE_WRITE,
        handle_debug_probe_write,
        schema=DEBUG_PROBE_WRITE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DEBUG_PROBE,
        handle_debug_probe,
        schema=DEBUG_PROBE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for service in (
        SERVICE_DEBUG_DYNAMIC_EXPORT,
        SERVICE_DEBUG_DYNAMIC_CHARGE,
        SERVICE_DEBUG_PROBE,
        SERVICE_DEBUG_PROBE_WRITE,
        SERVICE_CHARGE_START,
        SERVICE_CHARGE_STOP,
        SERVICE_DISCHARGE_START,
        SERVICE_DISCHARGE_STOP,
        SERVICE_CHARGE_FREEZE,
        SERVICE_DISCHARGE_FREEZE,
        SERVICE_IDLE,
        SERVICE_DEBUG_FREEZE,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
