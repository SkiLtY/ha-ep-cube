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
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
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
    # The state of charge Predbat's own plan reaches at the end of the export window
    # (from the planned SoC curve), as a whole percent rounded down. This is the
    # number the plan page shows as the slot's Limit %. `discharge_target_soc_pct`
    # (predbat.best_export_limit) is the limit Predbat would program into an
    # inverter, which sits a few points lower (50 against 55 on 2026-10-15).
    discharge_planned_end_soc_pct: int | None = None
    # `predbat.best_charge_end` is the end of the next plan slot, not of the whole
    # charge. These carry the end and the Limit % of the run of back-to-back slots
    # starting at `charge_window.start` (see `_charge_run`); None when the plan's
    # slot curve is missing. 2026-10-26: a 00:30 to 05:30 charge was cut off at
    # 02:00 because the first slot was taken as the whole window.
    charge_run_end: datetime | None = None
    charge_run_limit_pct: int | None = None
    # Where the plan's charging stops if it is charging right now; None when it is
    # not (or cannot be told). The limit on how long a charge freeze may hold.
    charge_hold_end: datetime | None = None


def read_plan(hass: HomeAssistant, now: datetime | None = None) -> PredbatPlan:
    """Read every Predbat entity in one snapshot and return a parsed plan.

    Raises PredbatStateError on malformed timestamp values. Missing entities
    are treated as "no plan" — Predbat may not have published yet (cold boot)
    or the user may have set `set_charge_window: False` for boot-safety.
    """
    charge_window = _read_window("predbat.best_charge_start", "predbat.best_charge_end", hass)
    charge_limit_pct = _read_int_opt("predbat.best_charge_limit", hass)

    discharge_window = _read_window("predbat.best_export_start", "predbat.best_export_end", hass)
    discharge_target_soc_pct = _read_int_opt("predbat.best_export_limit", hass)
    discharge_planned_end_soc_pct = (
        _planned_soc_pct_at(hass, discharge_window.end) if discharge_window is not None else None
    )

    charge_run_end, charge_run_limit_pct = (
        _charge_run(hass, charge_window) if charge_window is not None else (None, None)
    )

    return PredbatPlan(
        charge_enabled=charge_window is not None,
        charge_window=charge_window,
        charge_limit_pct=charge_limit_pct,
        discharge_enabled=discharge_window is not None,
        discharge_window=discharge_window,
        discharge_target_soc_pct=discharge_target_soc_pct,
        discharge_planned_end_soc_pct=discharge_planned_end_soc_pct,
        charge_run_end=charge_run_end,
        charge_run_limit_pct=charge_run_limit_pct,
        charge_hold_end=_charge_hold_end(hass, now or dt_util.utcnow()),
    )


def _charge_run(
    hass: HomeAssistant, window: PredbatWindow
) -> tuple[datetime | None, int | None]:
    """End and Limit % of the charge that starts with `window`, over several slots.

    `predbat.best_charge_limit` carries the planned limit as change points
    (`results`: {slot start: limit %}, 0 = no charge). The run goes on from the
    end of the first slot while the next slot still charges to at least the limit
    reached so far (a drop means Predbat wants something else there, so the run
    stops and the slot boundary stays the end). Returns (None, None) when the
    curve is missing or has nothing for the window.
    """
    steps = _charge_steps(hass)
    if steps is None:
        return None, None

    def value_at(when: datetime) -> float | None:
        found = None
        for stamp, value in steps:
            if stamp <= when:
                found = value
        return found

    run_limit = value_at(window.start)
    if run_limit is None or run_limit <= 0:
        return None, None
    end = window.end
    while True:
        value = value_at(end)
        if value is None or value <= 0 or value < run_limit:
            break
        run_limit = max(run_limit, value)
        following = [stamp for stamp, _ in steps if stamp > end]
        if not following:
            break                      # no known end: do not guess
        end = following[0]
    return end, int(math.ceil(run_limit))


def _charge_steps(hass: HomeAssistant) -> list[tuple[datetime, float]] | None:
    """The planned charge limit as sorted (slot start, limit %) change points,
    or None when `predbat.best_charge_limit` has no curve."""
    state = _state_obj(hass, "predbat.best_charge_limit")
    results = state.attributes.get("results") if state is not None else None
    if not isinstance(results, dict):
        return None
    steps: list[tuple[datetime, float]] = []
    for key, value in results.items():
        stamp = dt_util.parse_datetime(str(key))
        try:
            steps.append((stamp, float(value)))
        except (TypeError, ValueError):
            continue
    return sorted((s for s in steps if s[0] is not None), key=lambda s: s[0])


def _charge_hold_end(hass: HomeAssistant, now: datetime) -> datetime | None:
    """When the plan's charging stops, if the plan is charging at `now`.

    None when the curve is missing, the plan has no charge at `now` (limit 0),
    or no later point ends it (an end is never guessed). A freeze or hold is
    only legitimate inside a planned charge: `best_charge_start`/`_end` can be a
    stale window from an earlier optimiser pass (10 Nov 2026: 01:00 to 13:30
    published while the plan charged only until 05:30), and holding to that end
    kept the reserve at 100% for hours.
    """
    steps = _charge_steps(hass)
    if not steps:
        return None
    current = None
    for stamp, value in steps:
        if stamp <= now:
            current = value
    if current is None or current <= 0:
        return None
    for stamp, value in steps:
        if stamp > now and value <= 0:
            return stamp
    return None


def _planned_soc_pct_at(hass: HomeAssistant, when: datetime) -> int | None:
    """Predbat's planned SoC (whole percent, rounded down) at `when`.

    Reads the planned SoC curve in `predbat.soc_kw_best` (kWh keyed by timestamp,
    five-minute steps) and divides by its `soc_max` attribute. Returns None if
    the curve, the capacity or a point within 2.5 minutes of `when` is missing.
    """
    state = _state_obj(hass, "predbat.soc_kw_best")
    if state is None:
        return None
    results = state.attributes.get("results")
    try:
        capacity = float(state.attributes.get("soc_max"))
    except (TypeError, ValueError):
        return None
    if not isinstance(results, dict) or capacity <= 0:
        return None
    best: tuple[float, float] | None = None
    for key, kwh in results.items():
        stamp = dt_util.parse_datetime(str(key))
        if stamp is None:
            continue
        gap = abs((stamp - when).total_seconds())
        if gap <= 150 and (best is None or gap < best[0]):
            try:
                best = (gap, float(kwh))
            except (TypeError, ValueError):
                continue
    if best is None:
        return None
    # Rounded down with a quarter-point tolerance: the curve is published to three
    # decimals, so a plan limit of 55 can read 54.98 (seen) or 55.7 (also seen).
    return max(0, min(100, int(math.floor(best[1] / capacity * 100 + 0.25))))


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


# Predbat's monitor-only switch: on = Predbat reads but never writes to the cube.
PREDBAT_READ_ONLY_SWITCH = "switch.predbat_set_read_only"


# Where the PredbatPresence tracker lives in hass.data (a top-level key of its own,
# so it does not show up among the config entries stored under DOMAIN).
PREDBAT_PRESENCE_KEY = "ep_cube_predbat_presence"
# A missing switch counts as "Predbat is just not publishing yet" for this long after
# it was last seen, so a Home Assistant or Predbat restart (minutes) is covered, while
# an uninstalled Predbat stops blocking the standalone services after a day.
PREDBAT_GONE_AFTER = timedelta(hours=24)
_PRESENCE_REFRESH = timedelta(minutes=10)
_PRESENCE_SAVE_EVERY = timedelta(hours=1)


class PredbatPresence:
    """Remembers, across restarts, when Predbat's read-only switch was last seen.

    Predbat publishes its entities itself, so after a Home Assistant restart they are
    missing until it republishes them. Without a memory, "no switch" looks the same as
    "no Predbat" and a forced action would slip through while Predbat is about to
    resume control."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store = Store(hass, 1, "ep_cube.predbat_presence")
        self._saved_at: datetime | None = None
        self.last_seen: datetime | None = None
        self._unsubs: list = []

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        raw = data.get("last_seen")
        self.last_seen = self._saved_at = dt_util.parse_datetime(raw) if raw else None
        self._unsubs = [
            async_track_state_change_event(
                self._hass, [PREDBAT_READ_ONLY_SWITCH], self._on_event
            ),
            async_track_time_interval(self._hass, self._on_tick, _PRESENCE_REFRESH),
        ]
        self.refresh()

    @callback
    def _on_event(self, _event: Any) -> None:
        self.refresh()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self.refresh()

    @callback
    def refresh(self) -> None:
        if self._hass.states.get(PREDBAT_READ_ONLY_SWITCH) is None:
            return
        now = dt_util.utcnow()
        self.last_seen = now
        if self._saved_at is None or now - self._saved_at >= _PRESENCE_SAVE_EVERY:
            self._saved_at = now
            self._store.async_delay_save(lambda: {"last_seen": now.isoformat()}, 5)

    @property
    def recently_seen(self) -> bool:
        return (
            self.last_seen is not None
            and dt_util.utcnow() - self.last_seen < PREDBAT_GONE_AFTER
        )

    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []


def predbat_controls_cube(hass: HomeAssistant) -> bool:
    """True unless we can tell Predbat is not driving the cube.

    The only "not driving" signals are Predbat's read-only switch reading "on"
    (monitor mode), or no sign of Predbat at all. Anything else is treated as control,
    so a forced action is refused rather than risked: the switch "off", "unavailable"
    or "unknown", or the switch missing while Predbat was seen recently (it has not
    republished since a restart). The predbat.* entities are not consulted; they
    vanish the same way."""
    switch = hass.states.get(PREDBAT_READ_ONLY_SWITCH)
    if switch is None:
        presence = hass.data.get(PREDBAT_PRESENCE_KEY)
        return presence is not None and presence.recently_seen
    return switch.state != "on"


def ensure_predbat_not_in_control(hass: HomeAssistant, action: str) -> None:
    """Guard for every standalone (non-Predbat) control service: refuse while
    Predbat is driving the cube, since its next plan would replace the action.
    Call this first in any new user-facing control service. Stop/cancel services
    must not call it, so a running override can always be ended."""
    if predbat_controls_cube(hass):
        raise HomeAssistantError(
            f"Predbat is controlling this cube (or has not reported its state yet), so "
            f"{action} would be replaced by its plan. Put Predbat in read-only mode "
            f"({PREDBAT_READ_ONLY_SWITCH}) or use Predbat instead."
        )
