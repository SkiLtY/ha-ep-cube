"""Predbat shim service layer.

Translates Predbat's rate-based, time-windowed control contract into EP Cube's
mode + TOU-schedule contract. See docs/ARCHITECTURE.md for the design.

State held per-shim:
  - baseline_mode / baseline_schedule: snapshot of user's "normal" config,
    captured the first time we override anything. Restored when an override ends.
  - active_override: dict describing what's currently in flight. None when idle.
  - revert_unsub: callback to cancel the auto-revert timer.

Idempotency: if a shim service is called with the same effective parameters as
the active override, no cloud write is issued.
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

from .api import EPCubeClient, EPCubeError
from .const import (
    DOMAIN,
    OPERATING_MODE_SELF_CONSUMPTION,
    OPERATING_MODE_TOU,
)

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

# Service schemas
WINDOWED_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("end_time"): cv.datetime,
        vol.Required("target_soc_pct"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("rate_w"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)

FREEZE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
        vol.Required("end_time"): cv.datetime,
    }
)

STOP_SCHEMA = vol.Schema({vol.Optional("device_id"): cv.string})


class PredbatShim:
    """Per-device Predbat shim. One instance per EP Cube config entry.

    All public methods are async and idempotent. Cloud writes are only issued
    when the effective state would change.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, client: EPCubeClient) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        self._baseline_mode: str | None = None
        self._baseline_schedule: dict[str, Any] | None = None
        self._active_override: dict[str, Any] | None = None
        self._revert_unsub = None

    @property
    def is_active(self) -> bool:
        return self._active_override is not None

    async def charge_start(
        self, end_time: datetime, target_soc_pct: float, rate_w: float | None = None
    ) -> None:
        """Force grid charge until end_time, targeting given SoC.

        rate_w is ignored — TOU charges at max rate. Documented in ARCHITECTURE.md.
        """
        if rate_w is not None:
            _LOGGER.debug(
                "charge_start: rate_w=%s ignored (TOU charges at max rate)", rate_w
            )

        params = {"kind": KIND_CHARGE, "end_time": end_time, "target_soc_pct": target_soc_pct}
        if self._matches_active(params):
            _LOGGER.debug("charge_start: idempotent no-op (same args as active override)")
            return

        await self._snapshot_baseline()
        schedule = self._build_override_schedule(
            grid_charge=True, target_soc_pct=target_soc_pct, end_time=end_time
        )
        await self._apply_override(OPERATING_MODE_TOU, schedule, params)
        self._schedule_revert(end_time)
        _LOGGER.info(
            "charge_start applied: end_time=%s target_soc=%.0f%%",
            end_time.isoformat(), target_soc_pct,
        )

    async def charge_stop(self) -> None:
        if self._active_override and self._active_override["kind"] == KIND_CHARGE:
            await self._revert_to_baseline()
            _LOGGER.info("charge_stop: reverted to baseline")
        else:
            _LOGGER.debug("charge_stop: no active charge override (no-op)")

    async def discharge_start(
        self, end_time: datetime, target_soc_pct: float, rate_w: float | None = None
    ) -> None:
        """Force discharge until end_time, targeting given SoC.

        Working assumption: a TOU 'peak' slot with grid_charge=False and a low
        target_soc_pct triggers grid export. Hardware reconciliation needed —
        may require operating_mode flip instead. See ARCHITECTURE.md.
        """
        if rate_w is not None:
            _LOGGER.debug(
                "discharge_start: rate_w=%s ignored (TOU discharges at max rate)", rate_w
            )

        params = {"kind": KIND_DISCHARGE, "end_time": end_time, "target_soc_pct": target_soc_pct}
        if self._matches_active(params):
            _LOGGER.debug("discharge_start: idempotent no-op")
            return

        await self._snapshot_baseline()
        schedule = self._build_override_schedule(
            grid_charge=False,
            target_soc_pct=target_soc_pct,
            end_time=end_time,
            slot_type="peak",
        )
        await self._apply_override(OPERATING_MODE_TOU, schedule, params)
        self._schedule_revert(end_time)
        _LOGGER.info(
            "discharge_start applied: end_time=%s target_soc=%.0f%%",
            end_time.isoformat(), target_soc_pct,
        )

    async def discharge_stop(self) -> None:
        if self._active_override and self._active_override["kind"] == KIND_DISCHARGE:
            await self._revert_to_baseline()
            _LOGGER.info("discharge_stop: reverted to baseline")
        else:
            _LOGGER.debug("discharge_stop: no active discharge override (no-op)")

    async def charge_freeze(self, end_time: datetime) -> None:
        """Hold battery at current SoC until end_time.

        Implementation: set reserve_soc to current SoC + self_consumption mode,
        which prevents discharge below current. Charging from solar still possible.
        """
        params = {"kind": KIND_FREEZE, "end_time": end_time}
        if self._matches_active(params):
            return

        # Read current SoC to set as reserve floor
        try:
            status = await self.client.get_status()
            current_soc = status.soc_pct
        except EPCubeError as err:
            _LOGGER.error("charge_freeze: failed to read current SoC: %s", err)
            raise

        await self._snapshot_baseline()
        await self.client.set_operating_mode(
            OPERATING_MODE_SELF_CONSUMPTION, reserve_soc_pct=current_soc
        )
        self._active_override = {
            **params,
            "applied_mode": OPERATING_MODE_SELF_CONSUMPTION,
            "applied_reserve_soc": current_soc,
        }
        self._schedule_revert(end_time)
        _LOGGER.info("charge_freeze applied: hold at %.0f%% until %s", current_soc, end_time.isoformat())

    async def discharge_freeze(self, end_time: datetime) -> None:
        """Alias for charge_freeze — semantics are identical (battery idle).

        Predbat distinguishes the two semantically; we treat them the same.
        """
        await self.charge_freeze(end_time)

    async def idle(self) -> None:
        """Restore the baseline TOU schedule. Equivalent to '_stop' for any active override."""
        if self.is_active:
            await self._revert_to_baseline()
            _LOGGER.info("idle: reverted to baseline")
        else:
            _LOGGER.debug("idle: no active override (no-op)")

    def cancel_revert_timer(self) -> None:
        """Public cancel — used during integration teardown."""
        self._cancel_revert()

    # --- internals -------------------------------------------------------

    def _matches_active(self, params: dict[str, Any]) -> bool:
        """Idempotency check — would this call change the state?"""
        if not self._active_override:
            return False
        for key, value in params.items():
            if self._active_override.get(key) != value:
                return False
        return True

    async def _snapshot_baseline(self) -> None:
        """Capture user's normal mode + TOU schedule. Lazy — runs only once per shim
        lifetime, on first override. Persists across overrides."""
        if self._baseline_schedule is not None:
            return
        try:
            status = await self.client.get_status()
            schedule = await self.client.get_tou_schedule()
        except EPCubeError as err:
            _LOGGER.error("baseline snapshot failed: %s", err)
            raise
        self._baseline_mode = status.operating_mode
        self._baseline_schedule = schedule
        _LOGGER.info("baseline snapshotted: mode=%s schedule=%s slots", self._baseline_mode, _slot_count(schedule))

    async def _apply_override(
        self, mode: str, schedule: dict[str, Any], params: dict[str, Any]
    ) -> None:
        """Atomic-ish override: set mode + schedule, then record active state."""
        await self.client.set_operating_mode(mode)
        await self.client.set_tou_schedule(schedule)
        self._active_override = {**params, "applied_mode": mode, "applied_schedule": schedule}

    async def _revert_to_baseline(self) -> None:
        """Restore the snapshotted baseline. Cancels any pending revert timer."""
        self._cancel_revert()
        if self._baseline_schedule is None or self._baseline_mode is None:
            _LOGGER.warning("_revert_to_baseline: no baseline available — leaving cloud as-is")
            self._active_override = None
            return
        try:
            await self.client.set_tou_schedule(self._baseline_schedule)
            await self.client.set_operating_mode(self._baseline_mode)
        except EPCubeError as err:
            _LOGGER.error(
                "revert FAILED — battery may be stuck on override schedule. err=%s", err
            )
            # Don't clear active_override — leave it so a subsequent retry can target the right state
            raise
        self._active_override = None

    def _schedule_revert(self, end_time: datetime) -> None:
        """Schedule a one-shot revert at end_time. Replaces any previous schedule."""
        self._cancel_revert()
        # Ensure UTC for HA's scheduler
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
            # Already logged; nothing more to do here
            pass

    def _build_override_schedule(
        self,
        *,
        grid_charge: bool,
        target_soc_pct: float,
        end_time: datetime,
        slot_type: str = "off_peak",
    ) -> dict[str, Any]:
        """Compose a TOU schedule with one override slot covering now → end_time,
        plus the user's baseline filling the rest of the day.

        Note: this v1 version assumes the override slot doesn't span a day boundary.
        Predbat slots are typically 30 min so this is fine in practice.
        """
        now_local = dt_util.as_local(dt_util.utcnow())
        end_local = dt_util.as_local(end_time)
        override_slot = {
            "start": now_local.strftime("%H:%M"),
            "end": end_local.strftime("%H:%M"),
            "type": slot_type,
            "grid_charge": grid_charge,
            "target_soc_pct": target_soc_pct,
            "_predbat_override": True,
        }
        # v1 simplification: replace today's schedule with [override, fillers from baseline].
        # Tomorrow's schedule (i.e. weekend portion) untouched.
        baseline = self._baseline_schedule or {}
        is_weekend = now_local.weekday() >= 5
        day_key = "weekend" if is_weekend else "weekday"
        baseline_slots = baseline.get(day_key, [])
        merged = _merge_override(baseline_slots, override_slot)
        return {
            **baseline,
            day_key: merged,
        }


def _slot_count(schedule: dict[str, Any]) -> int:
    return sum(len(v) for v in schedule.values() if isinstance(v, list))


def _merge_override(baseline_slots: list[dict[str, Any]], override: dict[str, Any]) -> list[dict[str, Any]]:
    """Insert override into baseline slots, splitting any baseline slot it overlaps.

    v1 implementation — string compares HH:MM. Good enough for same-day overrides;
    won't handle midnight-spanning slots correctly. Documented limitation.
    """
    o_start, o_end = override["start"], override["end"]
    out: list[dict[str, Any]] = []
    for slot in baseline_slots:
        s, e = slot["start"], slot["end"]
        if e <= o_start or s >= o_end:
            # No overlap
            out.append(slot)
            continue
        # Overlap: keep the non-overlapping flanks of the baseline slot
        if s < o_start:
            out.append({**slot, "end": o_start})
        if e > o_end:
            out.append({**slot, "start": o_end})
    out.append(override)
    out.sort(key=lambda x: x["start"])
    return out


# ---------- service registration ----------


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the Predbat shim services on the HA service bus.

    Services dispatch to the shim of the targeted EP Cube device. If only one
    EP Cube is configured, device_id can be omitted.
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
                if shim.client.device_id == target_device_id:
                    return shim
            raise vol.Invalid(f"no EP Cube configured for device_id={target_device_id}")
        if len(shims) > 1:
            raise vol.Invalid(
                "multiple EP Cubes configured — pass device_id to disambiguate"
            )
        return next(iter(shims.values()))

    async def handle_charge_start(call: ServiceCall) -> None:
        shim = _resolve_shim(call)
        await shim.charge_start(
            end_time=call.data["end_time"],
            target_soc_pct=call.data["target_soc_pct"],
            rate_w=call.data.get("rate_w"),
        )

    async def handle_charge_stop(call: ServiceCall) -> None:
        await _resolve_shim(call).charge_stop()

    async def handle_discharge_start(call: ServiceCall) -> None:
        shim = _resolve_shim(call)
        await shim.discharge_start(
            end_time=call.data["end_time"],
            target_soc_pct=call.data["target_soc_pct"],
            rate_w=call.data.get("rate_w"),
        )

    async def handle_discharge_stop(call: ServiceCall) -> None:
        await _resolve_shim(call).discharge_stop()

    async def handle_charge_freeze(call: ServiceCall) -> None:
        await _resolve_shim(call).charge_freeze(end_time=call.data["end_time"])

    async def handle_discharge_freeze(call: ServiceCall) -> None:
        await _resolve_shim(call).discharge_freeze(end_time=call.data["end_time"])

    async def handle_idle(call: ServiceCall) -> None:
        await _resolve_shim(call).idle()

    hass.services.async_register(DOMAIN, SERVICE_CHARGE_START, handle_charge_start, schema=WINDOWED_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_STOP, handle_charge_stop, schema=STOP_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCHARGE_START, handle_discharge_start, schema=WINDOWED_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCHARGE_STOP, handle_discharge_stop, schema=STOP_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CHARGE_FREEZE, handle_charge_freeze, schema=FREEZE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DISCHARGE_FREEZE, handle_discharge_freeze, schema=FREEZE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_IDLE, handle_idle, schema=STOP_SCHEMA)


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
