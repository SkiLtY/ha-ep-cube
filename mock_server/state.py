"""In-memory device state for the mock EP Cube cloud."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _default_tou_schedule() -> dict[str, Any]:
    """A plausible default TOU schedule — represents a 'normal' user setup
    that the Predbat shim will snapshot as baseline before overriding."""
    return {
        "weekday": [
            {"start": "00:00", "end": "07:00", "type": "off_peak", "grid_charge": False, "target_soc_pct": 60},
            {"start": "07:00", "end": "16:00", "type": "mid_peak", "grid_charge": False, "target_soc_pct": 60},
            {"start": "16:00", "end": "19:00", "type": "peak", "grid_charge": False, "target_soc_pct": 60},
            {"start": "19:00", "end": "24:00", "type": "off_peak", "grid_charge": False, "target_soc_pct": 60},
        ],
        "weekend": [
            {"start": "00:00", "end": "24:00", "type": "off_peak", "grid_charge": False, "target_soc_pct": 60},
        ],
    }


@dataclass
class DeviceState:
    soc_pct: float = 55.0
    soc_kwh: float = 5.5
    capacity_kwh: float = 10.0
    battery_power_w: float = -300.0
    grid_power_w: float = -100.0
    solar_power_w: float = 1200.0
    load_power_w: float = 800.0
    operating_mode: str = "self_consumption"
    reserve_soc_pct: float = 20.0
    tou_schedule: dict[str, Any] = field(default_factory=_default_tou_schedule)

    def to_status_dict(self) -> dict[str, Any]:
        return {
            "soc_pct": self.soc_pct,
            "soc_kwh": self.soc_kwh,
            "capacity_kwh": self.capacity_kwh,
            "battery_power_w": self.battery_power_w,
            "grid_power_w": self.grid_power_w,
            "solar_power_w": self.solar_power_w,
            "load_power_w": self.load_power_w,
            "operating_mode": self.operating_mode,
            "reserve_soc_pct": self.reserve_soc_pct,
        }


DEVICES: dict[str, DeviceState] = {
    "ep_cube_test_01": DeviceState(),
}
