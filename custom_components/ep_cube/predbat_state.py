"""Read Predbat's planned window from its published `predbat.*` entities.

Predbat (`has_service_api: True`) publishes the planned charge/export windows
to entities under the `predbat.` domain BEFORE firing the matching service
call. Empirically the service call carries empty `data {}` (apps.yaml has no
template `data:` keys), so the entities are the source of truth for the shim.

Upstream contract — `apps/predbat/output.py` (springfall2008/batpred; the
`nipar44/predbat_addon` Docker image is a repackage of the same source):

  Domain:        `predbat.` (NOT `sensor.`)
  No prefix:     bare names like `predbat.best_charge_start`. The
                 `inverter_type` / `ge_inverter` settings from apps.yaml do
                 NOT decorate these entity IDs.
  "best" plan:   `predbat.best_charge_*` / `predbat.best_export_*` carry the
                 lowest-cost plan Predbat has chosen. The unprefixed
                 `predbat.charge_*` entities are the *baseline* (predicted
                 with no Predbat actions) and must not be used for control.
  Time format:   state is `"HH:MM:SS"` clock-time, attributes.timestamp is
                 ISO `"%Y-%m-%dT%H:%M:%S%z"`. We read the attribute — it is
                 unambiguous across day boundaries.
  No-plan:       state is `""` and attributes.timestamp is `None`.
  No discharge:  Predbat names it "export", not "discharge". Force-export and
                 force-discharge are the same operation from the inverter's
                 perspective; the shim's `discharge_*` services consume
                 Predbat's `best_export_*` window.
  No idle:       Idle / hold-SOC is not published as a window entity. The
                 shim doesn't action it either.
  No reserve:    `reserve` is internal to Predbat and not published.

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

_LOGGER = logging.getLogger(__name__)


class PredbatStateError(Exception):
    """Raised when a required Predbat entity is missing or malformed."""


@dataclass(frozen=True)
class PredbatWindow:
    """A single planned window. `start`/`end` are absolute datetimes."""

    start: datetime
    end: datetime

    @property
    def is_no_plan(self) -> bool:
        return self.start == self.end


@dataclass(frozen=True)
class PredbatPlan:
    """Snapshot of Predbat's currently published "best" plan."""

    charge_enabled: bool
    charge_window: PredbatWindow | None
    charge_limit_pct: int | None

    discharge_enabled: bool
    discharge_window: PredbatWindow | None
    discharge_target_soc_pct: int | None


def read_plan(hass: HomeAssistant) -> PredbatPlan:
    """Read every Predbat entity in one snapshot and return a parsed plan.

    Raises PredbatStateError on malformed timestamp values. Missing entities
    are treated as "no plan" — Predbat may not have published yet (cold boot)
    or the user may have set `set_charge_window: False` for boot-safety.
    """
    charge_window = _read_window("predbat.best_charge_start", "predbat.best_charge_end", hass)
    charge_limit_pct = _read_int_opt("predbat.best_charge_limit", hass)

    discharge_window = _read_window("predbat.best_export_start", "predbat.best_export_end", hass)
    discharge_target_soc_pct = _read_int_opt("predbat.best_export_limit", hass)

    return PredbatPlan(
        charge_enabled=charge_window is not None,
        charge_window=charge_window,
        charge_limit_pct=charge_limit_pct,
        discharge_enabled=discharge_window is not None,
        discharge_window=discharge_window,
        discharge_target_soc_pct=discharge_target_soc_pct,
    )


def _state_obj(hass: HomeAssistant, entity_id: str):
    state = hass.states.get(entity_id)
    if state is None:
        return None
    if state.state in ("unknown", "unavailable", None):
        return None
    return state


def _read_int_opt(entity_id: str, hass: HomeAssistant) -> int | None:
    state = _state_obj(hass, entity_id)
    if state is None or state.state == "":
        return None
    try:
        return int(float(state.state))
    except (TypeError, ValueError):
        _LOGGER.warning("predbat entity %s has non-numeric value %r", entity_id, state.state)
        return None


def _read_window(
    start_entity: str,
    end_entity: str,
    hass: HomeAssistant,
) -> PredbatWindow | None:
    start_dt = _read_timestamp(start_entity, hass)
    end_dt = _read_timestamp(end_entity, hass)
    if start_dt is None or end_dt is None:
        return None
    # Upstream publishes already-absolute timestamps, so no wrap-around fix-up
    # is needed. A degenerate end<=start means "no useful plan" — surface as
    # no-plan rather than a backward window.
    if end_dt <= start_dt:
        _LOGGER.debug(
            "predbat window %s..%s collapses to zero/negative — treating as no plan",
            start_entity, end_entity,
        )
        return None
    return PredbatWindow(start=start_dt, end=end_dt)


def _read_timestamp(entity_id: str, hass: HomeAssistant) -> datetime | None:
    """Return the `attributes.timestamp` ISO datetime, or None for no-plan.

    Upstream sets state="" and attributes.timestamp=None when no charge/export
    is planned within the forecast horizon (output.py:2273-2276 / 2102-2105).
    """
    state = _state_obj(hass, entity_id)
    if state is None:
        return None
    ts_raw = state.attributes.get("timestamp")
    if ts_raw is None or ts_raw == "":
        return None
    try:
        dt = datetime.fromisoformat(ts_raw)
    except (TypeError, ValueError) as err:
        raise PredbatStateError(
            f"malformed timestamp on {entity_id}: {ts_raw!r} ({err})"
        ) from err
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt


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
    }


def _describe_window(w: PredbatWindow | None) -> str | None:
    if w is None:
        return None
    return f"{w.start.isoformat()}→{w.end.isoformat()}"
