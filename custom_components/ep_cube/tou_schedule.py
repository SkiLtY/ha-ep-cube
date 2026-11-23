"""The cube's Time of Use schedule, read from `getSwitchMode`.

The cloud sends three lists of slots (off-peak, mid-peak, peak) for working days
and another three for non-work days, plus a third pair of sets for the clock
change. A slot is a string `"HH:MM_HH:MM_price"`. `activeWeek` and
`activeWeekNonWorkDay` say which weekdays (1 = Monday ... 7 = Sunday) use which
set. The cube sends bands and prices only, no charge or discharge action, so this
module reports the band and leaves the meaning to the user.

Read-only: nothing here writes to the cube.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

BAND_OFF_PEAK = "off_peak"
BAND_MID_PEAK = "mid_peak"
BAND_PEAK = "peak"
BAND_NONE = "none"
BANDS = (BAND_OFF_PEAK, BAND_MID_PEAK, BAND_PEAK)

# Wire key prefixes. The clock-change set carries a `dayLight` prefix.
_LIST_KEYS = {
    BAND_OFF_PEAK: "offPeakTimeList",
    BAND_MID_PEAK: "midPeakTimeList",
    BAND_PEAK: "peakTimeList",
}
_DAY_MINUTES = 24 * 60


@dataclass(frozen=True, slots=True)
class TouSlot:
    band: str
    start: int   # minutes after midnight
    end: int     # minutes after midnight; 1440 means the end of the day
    price: float | None

    @property
    def wraps(self) -> bool:
        """True when the slot runs past midnight (for example 23:30 to 05:30)."""
        return self.end <= self.start and not (self.start == 0 and self.end == _DAY_MINUTES)


@dataclass(frozen=True, slots=True)
class TouDaySet:
    """The slots that apply on a set of weekdays."""

    days: frozenset[int]
    slots: tuple[TouSlot, ...]


@dataclass(frozen=True, slots=True)
class TouSchedule:
    work_days: TouDaySet
    non_work_days: TouDaySet
    dst_active: bool

    def day_set(self, weekday: int) -> TouDaySet | None:
        """The set for an ISO weekday (1 = Monday), or None if no set claims it."""
        if weekday in self.work_days.days:
            return self.work_days
        if weekday in self.non_work_days.days:
            return self.non_work_days
        return None

    @property
    def has_slots(self) -> bool:
        return bool(self.work_days.slots or self.non_work_days.slots)


def _minutes(text: str) -> int | None:
    """`"05:30"` -> 330. `"24:00"` is allowed as an end of day."""
    try:
        hours, minutes = text.strip().split(":")[:2]
        value = int(hours) * 60 + int(minutes)
    except (ValueError, AttributeError):
        return None
    return value if 0 <= value <= _DAY_MINUTES else None


def parse_slot(band: str, raw: Any) -> TouSlot | None:
    """Parse one `"HH:MM_HH:MM_price"` string; None if it is not in that shape."""
    if not isinstance(raw, str):
        return None
    parts = raw.split("_")
    if len(parts) < 2:
        return None
    start, end = _minutes(parts[0]), _minutes(parts[1])
    if start is None or end is None:
        return None
    price: float | None = None
    if len(parts) >= 3:
        try:
            price = float(parts[2])
        except ValueError:
            price = None
    return TouSlot(band=band, start=start, end=end, price=price)


def _days(raw: Any) -> frozenset[int]:
    out: set[int] = set()
    for item in raw if isinstance(raw, (list, tuple)) else ():
        try:
            day = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7:
            out.add(day)
    return frozenset(out)


def _slots(mode: dict[str, Any], prefix: str, suffix: str) -> tuple[TouSlot, ...]:
    out: list[TouSlot] = []
    for band, key in _LIST_KEYS.items():
        # dayLight + OffPeakTimeList ... : the clock-change keys capitalise the first letter.
        name = key if not prefix else prefix + key[0].upper() + key[1:]
        for raw in mode.get(name + suffix) or ():
            slot = parse_slot(band, raw)
            if slot is not None:
                out.append(slot)
    out.sort(key=lambda s: (s.start, s.end))
    return tuple(out)


def parse_schedule(mode: dict[str, Any]) -> TouSchedule | None:
    """Build the schedule from a getSwitchMode payload; None if it carries none.

    While the clock-change flag (`dayLightSavingTime`) is on, the cube uses the
    `dayLight...` lists instead of the standard ones.
    """
    if not isinstance(mode, dict) or not any(
        key in mode for key in ("offPeakTimeList", "midPeakTimeList", "peakTimeList")
    ):
        return None
    dst = bool(mode.get("dayLightSavingTime"))
    prefix = "dayLight" if dst else ""
    work = TouDaySet(
        days=_days(mode.get("dayLightActiveWeek" if dst else "activeWeek")),
        slots=_slots(mode, prefix, ""),
    )
    non_work = TouDaySet(
        days=_days(mode.get("dayLightActiveWeekNonWorkDay" if dst else "activeWeekNonWorkDay")),
        slots=_slots(mode, prefix, "NonWorkDay"),
    )
    return TouSchedule(work_days=work, non_work_days=non_work, dst_active=dst)


def _slot_covers(slot: TouSlot, minute: int, *, same_day: bool) -> bool:
    """Whether `slot` covers `minute`. `same_day` is False when the slot started the
    previous day, so only its after-midnight part counts."""
    if slot.wraps:
        return minute >= slot.start if same_day else minute < slot.end
    return same_day and slot.start <= minute < slot.end


def band_at(schedule: TouSchedule, when: datetime) -> TouSlot | None:
    """The slot in force at `when` (local time), or None when no slot covers it."""
    minute = when.hour * 60 + when.minute
    today = schedule.day_set(when.isoweekday())
    if today is not None:
        for slot in today.slots:
            if _slot_covers(slot, minute, same_day=True):
                return slot
    yesterday = schedule.day_set((when.isoweekday() - 2) % 7 + 1)
    if yesterday is not None:
        for slot in yesterday.slots:
            if slot.wraps and _slot_covers(slot, minute, same_day=False):
                return slot
    return None


def next_change(schedule: TouSchedule, when: datetime) -> tuple[datetime, str] | None:
    """When the band next changes (looking up to a week ahead) and what it changes to.

    The band label is `none` when nothing covers that time. None if the band never
    changes, or the schedule holds no slots.
    """
    if not schedule.has_slots:
        return None
    current = band_at(schedule, when)
    now_label = current.band if current else BAND_NONE
    start = when.replace(second=0, microsecond=0)
    # Only slot edges can change the band, so collect every edge in the next 7 days.
    edges: set[int] = set()
    for day_offset in range(8):
        day = start + timedelta(days=day_offset)
        day_set = schedule.day_set(day.isoweekday())
        if day_set is None:
            continue
        for slot in day_set.slots:
            for minute in (slot.start, slot.end % _DAY_MINUTES):
                edges.add(day_offset * _DAY_MINUTES + minute)
    base = start.replace(hour=0, minute=0)
    now_offset = start.hour * 60 + start.minute
    for edge in sorted(edges):
        if edge <= now_offset:
            continue
        moment = base + timedelta(minutes=edge)
        slot = band_at(schedule, moment)
        label = slot.band if slot else BAND_NONE
        if label != now_label:
            return moment, label
    return None


def period_rows(schedule: TouSchedule) -> list[dict[str, Any]]:
    """The slots as plain rows for sensor attributes."""
    rows: list[dict[str, Any]] = []
    for group, day_set in (("work_days", schedule.work_days), ("non_work_days", schedule.non_work_days)):
        for slot in day_set.slots:
            rows.append(
                {
                    "days": group,
                    "band": slot.band,
                    "start": f"{slot.start // 60:02d}:{slot.start % 60:02d}",
                    "end": f"{(slot.end % _DAY_MINUTES) // 60:02d}:{slot.end % 60:02d}",
                    "price": slot.price,
                }
            )
    return rows
