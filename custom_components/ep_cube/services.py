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
import re
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .api import (
    EPCubeClient,
    EPCubeError,
    build_slot,
    payload_from_switch_mode_read,
)
from .const import (
    DEFAULT_TIER_PRICE_MID_PEAK,
    DEFAULT_TIER_PRICE_OFF_PEAK,
    DEFAULT_TIER_PRICE_PEAK,
    DOMAIN,
    OPERATING_MODE_TOU,
    SHIM_PRICE_MID_PEAK,
    SHIM_PRICE_OFF_PEAK,
    SHIM_PRICE_PEAK,
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
SERVICE_SET_TOU_SCHEDULE = "set_tou_schedule"

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
# slots at 0.01 / 0.20 / 1.00.
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


def _slot_wire_to_user(slot: str) -> str | None:
    """Convert a wire-format slot "HH:MM_HH:MM_PRICE" to user format
    "HH:MM-HH:MM". Returns None if the input doesn't have at least the two
    time components."""
    parts = str(slot).split("_")
    if len(parts) < 2:
        return None
    return f"{parts[0]}-{parts[1]}"


def parse_tou_schedule(state: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Parse a getSwitchMode response into the card-friendly nested shape.

    Strips shim-signature slots (so leftover overrides from in-flight Predbat
    windows don't leak into the user's view) and converts each remaining slot
    from wire format ("HH:MM_HH:MM_PRICE") to user format ("HH:MM-HH:MM").

    Returns::

        {
          "workday": {"peak": [...], "mid_peak": [...], "off_peak": [...]},
          "weekend": {"peak": [...], "mid_peak": [...], "off_peak": [...]},
        }

    Missing wire keys are treated as empty lists. Malformed slot strings are
    dropped silently.
    """
    cleaned, _ = _strip_shim_slots(state)
    result: dict[str, dict[str, list[str]]] = {
        "workday": {"peak": [], "mid_peak": [], "off_peak": []},
        "weekend": {"peak": [], "mid_peak": [], "off_peak": []},
    }
    for user_field, wire_key in _USER_FIELD_TO_WIRE_KEY.items():
        tier, _, profile = user_field.rpartition("_")
        if profile not in ("workday", "weekend") or tier not in ("peak", "mid_peak", "off_peak"):
            continue
        for raw in cleaned.get(wire_key, []) or []:
            user = _slot_wire_to_user(str(raw))
            if user is not None:
                result[profile][tier].append(user)
    return result


# ----------------------------------------------------------------------
# set_tou_schedule — user-facing TOU writer
# ----------------------------------------------------------------------
# User-facing slot format is "HH:MM-HH:MM" (single dash, no price). The cube
# wire format is "HH:MM_HH:MM_PRICE.PP" — we re-encode on write, preserving
# the existing tier's price where possible (so editing slots doesn't silently
# overwrite the user's tariff). Midnight-crossing slots are rejected (the
# cube probably tolerates them but the wire format isn't documented for
# end < start and Predbat's slots never need them either).
_USER_SLOT_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")

# Default price per tier when the cube currently has no slots in that tier
# (so no price to preserve). Match the mock + factory defaults — see const.py.
_DEFAULT_PRICE_BY_USER_FIELD: dict[str, float] = {
    "peak_workday": DEFAULT_TIER_PRICE_PEAK,
    "mid_peak_workday": DEFAULT_TIER_PRICE_MID_PEAK,
    "off_peak_workday": DEFAULT_TIER_PRICE_OFF_PEAK,
    "peak_weekend": DEFAULT_TIER_PRICE_PEAK,
    "mid_peak_weekend": DEFAULT_TIER_PRICE_MID_PEAK,
    "off_peak_weekend": DEFAULT_TIER_PRICE_OFF_PEAK,
}

# Map user field name → cube wire tier-list key.
_USER_FIELD_TO_WIRE_KEY: dict[str, str] = {
    "peak_workday": "peakTimeList",
    "mid_peak_workday": "midPeakTimeList",
    "off_peak_workday": "offPeakTimeList",
    "peak_weekend": "peakTimeListNonWorkDay",
    "mid_peak_weekend": "midPeakTimeListNonWorkDay",
    "off_peak_weekend": "offPeakTimeListNonWorkDay",
}

SET_TOU_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("peak_workday"): [cv.string],
        vol.Required("mid_peak_workday"): [cv.string],
        vol.Required("off_peak_workday"): [cv.string],
        vol.Required("peak_weekend"): [cv.string],
        vol.Required("mid_peak_weekend"): [cv.string],
        vol.Required("off_peak_weekend"): [cv.string],
        vol.Optional("switch_to_tou", default=False): cv.boolean,
    }
)


def _parse_user_slot(slot: str) -> tuple[int, int]:
    """Parse 'HH:MM-HH:MM' to (start_minutes, end_minutes). end > start required."""
    m = _USER_SLOT_RE.match(slot)
    if not m:
        raise ValueError(
            f"slot {slot!r} not in HH:MM-HH:MM format (e.g. '04:30-16:00')"
        )
    sh, sm, eh, em = (int(x) for x in m.groups())
    start = sh * 60 + sm
    end = eh * 60 + em
    if end <= start:
        # The cube (and EP Cube mobile app) doesn't support slots that cross
        # midnight — the wire format treats end <= start as invalid. Users
        # who want a "rest of day" slot should end at 23:59.
        raise ValueError(
            f"slot {slot!r} ends before it starts — "
            f"use 23:59 to end a slot at midnight "
            f"(the EP Cube doesn't support slots that cross midnight)"
        )
    return start, end


def _validate_day_profile(
    day_label: str,
    tiers: dict[str, list[str]],
) -> None:
    """Validate per-tier format + within-tier overlap + cross-tier overlap.
    Raises HomeAssistantError with a precise location on failure."""
    parsed_per_tier: dict[str, list[tuple[int, int]]] = {}
    for tier_label, slots in tiers.items():
        parsed: list[tuple[int, int]] = []
        for raw in slots:
            try:
                parsed.append(_parse_user_slot(raw))
            except ValueError as err:
                raise HomeAssistantError(f"{day_label} {tier_label}: {err}") from None
        # Within-tier overlap (sort by start, check adjacent).
        for prev, cur in zip(sorted(parsed), sorted(parsed)[1:]):
            if cur[0] < prev[1]:
                raise HomeAssistantError(
                    f"{day_label} {tier_label}: overlapping slots "
                    f"(slot starting {cur[0] // 60:02d}:{cur[0] % 60:02d} "
                    f"begins before previous slot ends at "
                    f"{prev[1] // 60:02d}:{prev[1] % 60:02d})"
                )
        parsed_per_tier[tier_label] = parsed

    # Cross-tier overlap — minute coverage map. 1440 minutes max so cheap.
    coverage: dict[int, str] = {}
    for tier_label, parsed in parsed_per_tier.items():
        for start, end in parsed:
            for minute in range(start, end):
                claimed_by = coverage.get(minute)
                if claimed_by is not None and claimed_by != tier_label:
                    raise HomeAssistantError(
                        f"{day_label}: {tier_label} overlaps {claimed_by} at "
                        f"{minute // 60:02d}:{minute % 60:02d}"
                    )
                coverage[minute] = tier_label


def _existing_house_price(slots: list[Any], default: float) -> float:
    """Return the price (per-2dp float) of the first non-shim slot in `slots`,
    falling back to `default` if none. Used so set_tou_schedule writes
    preserve whatever per-tier price the user already had on the cube."""
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
    return default


def _user_slot_to_wire(user_slot: str, price: float) -> str:
    """Convert 'HH:MM-HH:MM' + price → 'HH:MM_HH:MM_PRICE.PP' wire string."""
    start_str, end_str = user_slot.split("-", 1)
    return build_slot(start_str, end_str, price)


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
        self._active_override = None
        self._baseline = None

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
        self._baseline, stripped = _strip_shim_slots(live)
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
        #   - replace the target tier list with our appended version
        #   - honour the caller's allow_grid_charge intent
        return payload_from_switch_mode_read(
            self.client.dev_id,
            baseline,
            overrides={
                "workStatus": WORK_STATUS_TOU,
                tier_key: appended_tier,
                "allowChargingXiaGrid": "1" if allow_grid_charge else "0",
            },
        )

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
        if self._baseline is None:
            _LOGGER.warning("_revert_to_baseline: no baseline available — leaving cloud as-is")
            self._active_override = None
            return

        payload = payload_from_switch_mode_read(self.client.dev_id, self._baseline)
        try:
            await self.client.switch_mode(payload)
        except EPCubeError as err:
            _LOGGER.error(
                "revert FAILED — battery may be stuck on override. err=%s",
                err,
            )
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

    async def handle_set_tou_schedule(call: ServiceCall) -> None:
        """User-facing TOU schedule writer.

        Replaces the workday + weekend non-DST tier lists in one cube write.
        DST tier lists, reserve SoCs, day masks and per-tier prices are
        preserved from the cube's current state. The cube's workStatus is
        left alone unless `switch_to_tou=true`.

        If a Predbat shim override is active when the user fires this
        service, the override is abandoned (user-wins) — Predbat will
        re-plan and re-apply if it still wants to on its next cycle.
        """
        shim = _resolve_shim(call)

        user_tiers = {
            field: list(call.data.get(field) or [])
            for field in _USER_FIELD_TO_WIRE_KEY
        }

        # Validate format + within-tier + cross-tier overlap before any cloud I/O.
        _validate_day_profile(
            "workday",
            {f: user_tiers[f] for f in ("peak_workday", "mid_peak_workday", "off_peak_workday")},
        )
        _validate_day_profile(
            "weekend",
            {f: user_tiers[f] for f in ("peak_weekend", "mid_peak_weekend", "off_peak_weekend")},
        )

        # User-wins: clear any in-flight shim override without writing to the
        # cube (the override's revert would clobber what we're about to write).
        if shim.is_active:
            _LOGGER.info("set_tou_schedule: abandoning active shim override (user-wins)")
            shim.abandon_override()

        # Read current state so we preserve DST tier lists + day masks + prices.
        try:
            live = await shim.client.get_switch_mode()
        except EPCubeError as err:
            raise HomeAssistantError(
                f"cannot read current cube state: {err}"
            ) from err

        # Drop stale shim-signature slots so they don't leak back into the write.
        live_clean, _ = _strip_shim_slots(live)

        # Per-tier price: preserve the cube's existing per-tier price; fall back
        # to per-tier defaults if the tier is currently empty.
        overrides: dict[str, Any] = {}
        for user_field, wire_key in _USER_FIELD_TO_WIRE_KEY.items():
            price = _existing_house_price(
                live_clean.get(wire_key, []) or [],
                _DEFAULT_PRICE_BY_USER_FIELD[user_field],
            )
            overrides[wire_key] = [
                _user_slot_to_wire(s, price) for s in user_tiers[user_field]
            ]

        if call.data.get("switch_to_tou"):
            overrides["workStatus"] = WORK_STATUS_TOU

        payload = payload_from_switch_mode_read(
            shim.client.dev_id, live_clean, overrides=overrides
        )

        try:
            await shim.client.switch_mode(payload)
        except EPCubeError as err:
            _LOGGER.error("set_tou_schedule write failed: %s", err)
            raise HomeAssistantError(f"cube rejected schedule write: {err}") from err

        _LOGGER.info(
            "set_tou_schedule applied: workday peak=%d mid=%d off=%d / "
            "weekend peak=%d mid=%d off=%d / switch_to_tou=%s",
            len(user_tiers["peak_workday"]),
            len(user_tiers["mid_peak_workday"]),
            len(user_tiers["off_peak_workday"]),
            len(user_tiers["peak_weekend"]),
            len(user_tiers["mid_peak_weekend"]),
            len(user_tiers["off_peak_weekend"]),
            bool(call.data.get("switch_to_tou")),
        )

        # Trigger a coordinator refresh so the operating-mode select's
        # extra_state_attributes (tou_schedule) and the editor card's
        # hydration reflect the new schedule immediately, not on the next
        # 30s tick.
        coordinator = hass.data.get(DOMAIN, {}).get(shim.entry_id, {}).get("coordinator")
        if coordinator is not None:
            await coordinator.async_request_refresh()

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
        SERVICE_SET_TOU_SCHEDULE,
        handle_set_tou_schedule,
        schema=SET_TOU_SCHEDULE_SCHEMA,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for service in (
        SERVICE_CHARGE_START,
        SERVICE_CHARGE_STOP,
        SERVICE_DISCHARGE_START,
        SERVICE_DISCHARGE_STOP,
        SERVICE_CHARGE_FREEZE,
        SERVICE_DISCHARGE_FREEZE,
        SERVICE_IDLE,
        SERVICE_DEBUG_FREEZE,
        SERVICE_SET_TOU_SCHEDULE,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
