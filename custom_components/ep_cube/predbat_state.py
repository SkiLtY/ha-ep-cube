"""Read Predbat's planned window from its dummy entities.

Predbat (`has_service_api: True`) publishes the planned charge/discharge window
to `sensor.predbat_<inverter_type>_<id>_*` entities BEFORE firing the matching
service call. Empirically the service call carries empty `data {}` (apps.yaml has
no template `data:` keys), so the entities are the source of truth for the shim.

Upstream contract — `apps/predbat/inverter.py`:
  - `*_start_time` / `*_end_time`           string "HH:MM:SS"
  - `charge_limit` / `discharge_target_soc` int 0–100
  - `scheduled_charge_enable` /
    `scheduled_discharge_enable`            string "on" | "off"
  - `reserve`                               int 0–100
  - "no plan" sentinel: start == end == "23:59:00"

This module is the only Predbat-aware layer in the integration. The shim
(services.py) consumes a parsed `PredbatPlan` and stays free of entity-naming
knowledge — so a future upstream rename is a one-file change here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    PREDBAT_ENTITY_PREFIX,
    PREDBAT_INVERTER_INDEX,
    PREDBAT_INVERTER_TYPE,
    PREDBAT_NO_PLAN_TIME,
)

_LOGGER = logging.getLogger(__name__)


class PredbatStateError(Exception):
    """Raised when a required Predbat entity is missing or malformed."""


@dataclass(frozen=True)
class PredbatWindow:
    """A single planned window. `start`/`end` are absolute datetimes (local TZ)."""

    start: datetime
    end: datetime

    @property
    def is_no_plan(self) -> bool:
        # Sentinel windows degenerate to zero-length on the next 23:59:00.
        return self.start == self.end


@dataclass(frozen=True)
class PredbatPlan:
    """Snapshot of Predbat's currently published plan."""

    charge_enabled: bool
    charge_window: PredbatWindow | None
    charge_limit_pct: int | None

    discharge_enabled: bool
    discharge_window: PredbatWindow | None
    discharge_target_soc_pct: int | None

    idle_window: PredbatWindow | None
    reserve_pct: int | None


def read_plan(hass: HomeAssistant) -> PredbatPlan:
    """Read every Predbat entity in one snapshot and return a parsed plan.

    Raises PredbatStateError if a required entity is missing — the shim should
    surface that to HA so the user sees the misconfiguration in the UI.
    """
    now_local = dt_util.as_local(dt_util.utcnow())

    charge_enabled = _read_enable("scheduled_charge_enable", hass)
    charge_window = _read_window("charge_start_time", "charge_end_time", hass, now_local)
    charge_limit_pct = _read_int_opt("charge_limit", hass)

    discharge_enabled = _read_enable("scheduled_discharge_enable", hass)
    discharge_window = _read_window(
        "discharge_start_time", "discharge_end_time", hass, now_local
    )
    discharge_target_soc_pct = _read_int_opt("discharge_target_soc", hass)

    idle_window = _read_window("idle_start_time", "idle_end_time", hass, now_local)
    reserve_pct = _read_int_opt("reserve", hass)

    return PredbatPlan(
        charge_enabled=charge_enabled,
        charge_window=charge_window,
        charge_limit_pct=charge_limit_pct,
        discharge_enabled=discharge_enabled,
        discharge_window=discharge_window,
        discharge_target_soc_pct=discharge_target_soc_pct,
        idle_window=idle_window,
        reserve_pct=reserve_pct,
    )


def _entity_id(name: str) -> str:
    return (
        f"sensor.{PREDBAT_ENTITY_PREFIX}_"
        f"{PREDBAT_INVERTER_TYPE}_{PREDBAT_INVERTER_INDEX}_{name}"
    )


def _get_state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    if state.state in ("unknown", "unavailable", None, ""):
        return None
    return state.state


def _read_enable(name: str, hass: HomeAssistant) -> bool:
    eid = _entity_id(name)
    raw = _get_state(hass, eid)
    if raw is None:
        # Missing enable flag is treated as "off" — defensive default.
        _LOGGER.debug("%s missing/unknown — treating as disabled", eid)
        return False
    return str(raw).strip().lower() == "on"


def _read_int_opt(name: str, hass: HomeAssistant) -> int | None:
    raw = _get_state(hass, _entity_id(name))
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        _LOGGER.warning("predbat entity %s has non-numeric value %r", name, raw)
        return None


def _read_window(
    start_name: str,
    end_name: str,
    hass: HomeAssistant,
    now_local: datetime,
) -> PredbatWindow | None:
    start_raw = _get_state(hass, _entity_id(start_name))
    end_raw = _get_state(hass, _entity_id(end_name))
    if start_raw is None or end_raw is None:
        return None
    if start_raw == PREDBAT_NO_PLAN_TIME and end_raw == PREDBAT_NO_PLAN_TIME:
        # Predbat's "no plan" sentinel.
        return None
    try:
        start_dt = _hms_to_datetime(start_raw, now_local)
        end_dt = _hms_to_datetime(end_raw, now_local)
    except ValueError as err:
        raise PredbatStateError(
            f"malformed window {start_name}={start_raw!r} {end_name}={end_raw!r}: {err}"
        ) from err
    # Window wraps midnight: end_dt was placed on the same date as start, push it.
    if end_dt <= start_dt:
        end_dt = end_dt + timedelta(days=1)
    return PredbatWindow(start=start_dt, end=end_dt)


def _hms_to_datetime(hms: str, now_local: datetime) -> datetime:
    """Resolve an HH:MM:SS string to today's local datetime.

    If the time has already passed today, returns tomorrow's occurrence — Predbat
    publishes upcoming windows, so a past time means "next time round".
    """
    parts = hms.split(":")
    if len(parts) != 3:
        raise ValueError(f"expected HH:MM:SS, got {hms!r}")
    h, m, s = (int(p) for p in parts)
    candidate = now_local.replace(hour=h, minute=m, second=s, microsecond=0)
    if candidate < now_local:
        candidate = candidate + timedelta(days=1)
    return candidate


def describe(plan: PredbatPlan) -> dict[str, Any]:
    """Compact dict for log lines — avoids dumping the dataclass repr."""
    return {
        "charge": {
            "enabled": plan.charge_enabled,
            "window": _describe_window(plan.charge_window),
            "limit_pct": plan.charge_limit_pct,
        },
        "discharge": {
            "enabled": plan.discharge_enabled,
            "window": _describe_window(plan.discharge_window),
            "target_soc_pct": plan.discharge_target_soc_pct,
        },
        "idle_window": _describe_window(plan.idle_window),
        "reserve_pct": plan.reserve_pct,
    }


def _describe_window(w: PredbatWindow | None) -> str | None:
    if w is None:
        return None
    return f"{w.start.isoformat()}→{w.end.isoformat()}"
