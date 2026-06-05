"""In-memory device state for the mock EP Cube cloud.

Shapes mirror the **mobile-app** surface on `monitoring-eu.epcube.com`
(`/api/device/*` + `/api/open/common/*`) that `api.py` and `captcha.py`
speak after the Phase 3.2 refactor. The older web-portal `/v1/api/home/*`
surface that the mock used to mimic is no longer touched by the
integration — see `docs/PHASE_3_2.md` "Wire-level discoveries".

Power values are stored as floats in kW internally for sim convenience
and rendered as **centi-kilowatt integers** on the wire (e.g. 0.64 kW →
`64`). The mobile API's quirky unit was confirmed empirically on
2026-05-21 — see api.py `_power_to_w` docstring. kWh values render as
plain floats. Schedule slots stay as the wire-format
`"HH:MM_HH:MM_price"` strings directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_envelope_ts() -> str:
    """`YYYY-MM-DD HH:MM:SS` (UTC) — the cloud's envelope timestamp format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ckw(value: float) -> int:
    """Render a kW value the way the mobile API does — centi-kW integer.

    0.64 kW → 64. Inverse of api.py `_power_to_w` (which multiplies wire
    value by 10 to get watts)."""
    return int(round(value * 100))


def build_slot(start_hhmm: str, end_hhmm: str, price: float) -> str:
    """Build a TOU slot wire string. Inverse of `parse_slot`."""
    return f"{start_hhmm}_{end_hhmm}_{price:.2f}"


def parse_slot(slot: str) -> tuple[str, str, float]:
    """Parse a TOU slot wire string into `(start, end, price)`."""
    start, end, price = slot.split("_")
    return start, end, float(price)


@dataclass
class TouSchedule:
    """The 16-array TOU payload that `setTimOfUse` POSTs and `getSwitchMode` reads back.

    Four parallel slot-list trios — (weekday, weekend) × (non-DST, DST) — plus
    the four `active*` day-mask arrays + `dayLightSavingTime` flag. Field names
    match the wire format verbatim, including their inconsistent ordering.
    """

    # Weekday (non-DST)
    peakTimeList: list[str] = field(default_factory=list)
    midPeakTimeList: list[str] = field(default_factory=list)
    offPeakTimeList: list[str] = field(default_factory=list)
    activeWeek: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    # Weekend (non-DST)
    peakTimeListNonWorkDay: list[str] = field(default_factory=list)
    midPeakTimeListNonWorkDay: list[str] = field(default_factory=list)
    offPeakTimeListNonWorkDay: list[str] = field(default_factory=list)
    activeWeekNonWorkDay: list[int] = field(default_factory=lambda: [6, 7])
    # DST flag + DST-active schedule (parallel structure)
    dayLightSavingTime: bool = False
    dayLightPeakTimeList: list[str] = field(default_factory=list)
    dayLightMidPeakTimeList: list[str] = field(default_factory=list)
    dayLightOffPeakTimeList: list[str] = field(default_factory=list)
    dayLightActiveWeek: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    dayLightPeakTimeListNonWorkDay: list[str] = field(default_factory=list)
    dayLightMidPeakTimeListNonWorkDay: list[str] = field(default_factory=list)
    dayLightOffPeakTimeListNonWorkDay: list[str] = field(default_factory=list)
    dayLightActiveWeekNonWorkDay: list[int] = field(default_factory=lambda: [6, 7])

    def to_wire(self) -> dict[str, Any]:
        return {
            "offPeakTimeList": list(self.offPeakTimeList),
            "midPeakTimeList": list(self.midPeakTimeList),
            "peakTimeList": list(self.peakTimeList),
            "activeWeek": list(self.activeWeek),
            "offPeakTimeListNonWorkDay": list(self.offPeakTimeListNonWorkDay),
            "midPeakTimeListNonWorkDay": list(self.midPeakTimeListNonWorkDay),
            "peakTimeListNonWorkDay": list(self.peakTimeListNonWorkDay),
            "activeWeekNonWorkDay": list(self.activeWeekNonWorkDay),
            "dayLightSavingTime": self.dayLightSavingTime,
            "dayLightOffPeakTimeList": list(self.dayLightOffPeakTimeList),
            "dayLightMidPeakTimeList": list(self.dayLightMidPeakTimeList),
            "dayLightPeakTimeList": list(self.dayLightPeakTimeList),
            "dayLightActiveWeek": list(self.dayLightActiveWeek),
            "dayLightOffPeakTimeListNonWorkDay": list(self.dayLightOffPeakTimeListNonWorkDay),
            "dayLightMidPeakTimeListNonWorkDay": list(self.dayLightMidPeakTimeListNonWorkDay),
            "dayLightPeakTimeListNonWorkDay": list(self.dayLightPeakTimeListNonWorkDay),
            "dayLightActiveWeekNonWorkDay": list(self.dayLightActiveWeekNonWorkDay),
        }

    def apply_wire(self, payload: dict[str, Any]) -> None:
        """Overwrite from a `setTimOfUse` request body. Silently ignores unknown keys."""
        for k in (
            "peakTimeList", "midPeakTimeList", "offPeakTimeList",
            "activeWeek",
            "peakTimeListNonWorkDay", "midPeakTimeListNonWorkDay", "offPeakTimeListNonWorkDay",
            "activeWeekNonWorkDay",
            "dayLightPeakTimeList", "dayLightMidPeakTimeList", "dayLightOffPeakTimeList",
            "dayLightActiveWeek",
            "dayLightPeakTimeListNonWorkDay", "dayLightMidPeakTimeListNonWorkDay", "dayLightOffPeakTimeListNonWorkDay",
            "dayLightActiveWeekNonWorkDay",
        ):
            if k in payload:
                setattr(self, k, list(payload[k]))
        if "dayLightSavingTime" in payload:
            self.dayLightSavingTime = bool(payload["dayLightSavingTime"])


def _default_tou() -> TouSchedule:
    """A plausible weekday-only TOU schedule. Mirrors the values seen in the
    pre-existing schedule on the real device at the start of the 2026-05-20
    capture session — gives us a recognisable baseline for shim auto-revert tests."""
    return TouSchedule(
        offPeakTimeList=[build_slot("00:30", "04:30", 0.05)],
        midPeakTimeList=[build_slot("04:30", "16:00", 0.25)],
        peakTimeList=[build_slot("16:00", "19:00", 0.40)],
    )


@dataclass
class DeviceState:
    """Single device. Field names match the wire format — `homeDeviceInfo` for
    the live values, `getSwitchMode` for mode/reserves/TOU, `getDeviceList` for
    metadata. Power values stored as floats in kW; converted to decimal strings
    on output. SoC stored as int 0-100.
    """

    # Identifiers
    devId: str = "5613"
    sgSn: str = "100100007001257120126"
    rtuSn: str = "RTU01230456789"

    # Liveness
    batterySoc: int = 55
    batteryCurrentElectricity: float = 11.0  # kWh stored
    systemCapacity_kwh: float = 20.0  # nominal; rendered as "20.0kWh" in getDeviceList
    batteryPackNum: int = 4

    # Power (kW, signed where applicable)
    solarPower: float = 1.20
    backUpPower: float = 0.80  # critical-load circuits
    nonBackUpPower: float = 0.00  # general house circuits
    gridPower: float = 0.00  # sign convention unknown on real cloud; we treat as signed
    generatorPower: float = 0.00
    evPower: float = 0.00

    # Energy today (kWh)
    solarElectricity: float = 0.35
    solarDcElectricity: float = 0.36  # pre-inverter; slightly > AC
    solarAcElectricity: float = 0.34  # post-inverter; slightly < DC
    backUpElectricity: float = 1.26
    nonBackUpElectricity: float = 0.00
    gridElectricity: float = 0.85
    generatorElectricity: float = 0.00
    evElectricity: float = 0.00
    selfHelpRate: float = 65.0  # self-consumption KPI %

    # Mode + reserves (workStatus: "1"=self_consumption, "2"=tou, "3"=backup)
    workStatus: str = "1"
    selfConsumptioinReserveSoc: int = 20  # sic — typo preserved; matches cube's apparent reset default
    backupPowerReserveSoc: int = 100
    evChargerReserveSoc: int = 50
    allowChargingXiaGrid: str = "1"  # string on read, int on write — see TOU extract

    # Schedule
    tou: TouSchedule = field(default_factory=_default_tou)
    touType: int = 0
    weatherWatch: str = "0"
    onlySave: str = "0"

    # Metadata
    softwareVersion: str = "V1.2.2"
    firmwareVersion: str = "02160239021920260323"
    isOnline: str = "1"
    isFault: str = "0"
    isAlert: str = "0"
    timeZone: str = "Europe/London"

    # ------------------------------------------------------------------
    # Wire-format renderers
    # ------------------------------------------------------------------
    def to_home_device_info(self) -> dict[str, Any]:
        """Match `GET /api/device/homeDeviceInfo?sgSn=...` response shape.

        Power fields are centi-kW integers (cube quirk — confirmed
        2026-05-21). Status `"1"` here is the device-online flag, NOT an
        envelope error code — see PHASE_3_2.md and api.py _request()."""
        return {
            "devId": self.devId,
            "status": "1",
            "workStatus": self.workStatus,
            "batterySoc": self.batterySoc,
            "batteryCurrentElectricity": self.batteryCurrentElectricity,
            "gridPowerFailureNum": 0,
            "offGridPowerSupplyTime": 0,
            "gridPower": _ckw(self.gridPower),
            "gridElectricity": self.gridElectricity,
            "solarPower": _ckw(self.solarPower),
            "solarElectricity": self.solarElectricity,
            "generatorPower": _ckw(self.generatorPower),
            "generatorElectricity": self.generatorElectricity,
            "evPower": _ckw(self.evPower),
            "evElectricity": self.evElectricity,
            "nonBackUpPower": _ckw(self.nonBackUpPower),
            "nonBackUpElectricity": self.nonBackUpElectricity,
            "backUpPower": _ckw(self.backUpPower),
            "backUpElectricity": self.backUpElectricity,
            "solarDcElectricity": self.solarDcElectricity,
            "solarAcElectricity": self.solarAcElectricity,
            "selfHelpRate": self.selfHelpRate,
            "isAlert": self.isAlert,
            "isFault": self.isFault,
            "defCreateTime": _now_envelope_ts(),
            "defTimeZone": "UTC",
            "fromCreateTime": _now_envelope_ts(),
            "fromTimeZone": self.timeZone,
            "fromType": "1",
            "systemStatus": 4,
            "backUpType": 1,
            "gridLight": "1",
            "generatorLight": "0",
            "evLight": "0",
            "ressNumber": 1,
            "isNewDevice": False,
            "version": self.firmwareVersion,
            "payloadVersion": 25,
            "isOnline": self.isOnline,
            "gridTotalPower": _ckw(self.gridPower),
            "gridHalfPower": 0,
            "solarFlow": _ckw(self.solarPower),
            "solarAcPower": 0,
            "solarDcPower": _ckw(self.solarPower),
            "generatorFlowPower": _ckw(self.generatorPower),
            "evFlowPower": _ckw(self.evPower),
            "nonBackUpFlowPower": _ckw(self.nonBackUpPower),
            "backUpFlowPower": _ckw(self.backUpPower),
            "backupLoadsMode": 1,
            "batteryPackNum": self.batteryPackNum,
            "devType": 1,
            "winterProtect": 85,
            "winterMode": 0,
            "off_ON_Grid_Hint": "Mock cloud — current behaviour explanation goes here.",
            # Phase 3.5 fields. gridPowerFailureNum + offGridPowerSupplyTime
            # are above (lines 201-202); earning fields added here.
            # unitDefault/unitSmallest/unitMulti included for forward-compat
            # with the eventual currency-locale work.
            "earningYesterday": 1.23,
            "unitDefault": "£",
            "unitSmallest": "p",
            "unitMulti": "100",
        }

    def to_switch_mode(self) -> dict[str, Any]:
        """Match `GET /api/device/getSwitchMode?devId=...` response shape."""
        data: dict[str, Any] = {
            "devId": self.devId,
            "weatherWatch": self.weatherWatch,
            "workStatus": self.workStatus,
            "onlySave": self.onlySave,
            "backupPowerReserveSoc": str(self.backupPowerReserveSoc),
            "selfConsumptioinReserveSoc": str(self.selfConsumptioinReserveSoc),
            "allowChargingXiaGrid": self.allowChargingXiaGrid,
        }
        data.update(self.tou.to_wire())
        data.update({
            "evChargerReserveSoc": self.evChargerReserveSoc,
            "existsSg": "1",
            "touType": self.touType,
        })
        return data

    def to_device_list_entry(self) -> dict[str, Any]:
        """Match one entry from `GET /api/device/deviceList`. Extensive metadata."""
        return {
            "id": self.devId,
            "sgSn": self.sgSn,
            "rtuSn": self.rtuSn,
            "snItems": self.sgSn,
            "name": "Mock EP Cube",
            "addressIds": "237,374",
            "city": "0.0",
            "mailCode": "AA1 1AA",
            "timeZone": self.timeZone,
            "lat": "51.5",
            "lon": "-0.1",
            "version": self.firmwareVersion,
            "softwareVersion": self.softwareVersion,
            "installUserId": "1",
            "userId": "1",
            "userEmail": "mo*****@example.com",
            "addressInfo": "Mock Street",
            "bingTime": "2026-05-20 16:58:33",
            "installTime": "2026-05-20 16:58:33",
            "isOnline": self.isOnline,
            "lastConnectTime": _now_envelope_ts(),
            "status": "1",
            "systemStatus": 4,
            "workStatus": self.workStatus,
            "workParam": "{}",
            "childDeviceStatus": "1100100000000000",
            "aotuUpdateFirmware": 1,
            "isFault": self.isFault,
            "networking": 1,
            "devType": 1,
            "dynamicPriceAuth": "0",
            "lastConnect": "1",
            "isParallel": "0",
            "hybridNum": 1,
            "rtuType": "0",
            "delFlag": "0",
            "testData": "0",
            # Derived
            "batteryType": "5.0kWh",
            "systemCapacity": f"{self.systemCapacity_kwh:.1f}kWh",
        }

    def to_query_data_electricity_v2(self, scope_type: int, date_str: str) -> dict[str, Any]:
        """Match one response from `GET /api/device/queryDataElectricityV2`.

        Returns the field set the EU firmware actually populates (verified
        2026-06-04 against the live cube). Fields that are 0 across all
        scopes on real EU firmware — batteryChargeElectricity /
        batteryDischargeElectricity, solarAcElectricity, nonBackUpElectricity,
        generator / EV — are included with 0 values so the wire shape stays
        honest. Instantaneous `*Power` fields are omitted: they only return
        meaningful values on a live snapshot, not a date-range query.

        Per-scope synthesis: today increments off `_stats_today_floor`
        across mock ticks so a dashboard staring at it looks alive;
        yesterday/month/year/total are static plausible numbers. The
        relationship total >= year >= month >= today is loosely preserved
        but not strictly enforced — mock-mode dev cares about shape parity
        more than physical correctness.
        """
        # Per-bucket multipliers from today's baseline. Picked to look
        # plausible for a ~6.5 kWp PV + 20 kWh battery system after a few
        # weeks of commissioning. Year and total are equal until year-roll,
        # which matches the real cube's behaviour today.
        if scope_type == 1:  # DAILY — today (or yesterday if date_str < today)
            from datetime import date as _date
            today_str = _date.today().strftime("%Y-%m-%d")
            if date_str == today_str:
                # Today values
                grid_from = 0.44
                grid_to = 7.88
                solar = 27.87
                backup = 18.47
                self_help = 98
            else:
                # Yesterday values — deliberately different from today
                grid_from = 2.53
                grid_to = 3.63
                solar = 20.42
                backup = 13.75
                self_help = 82
        elif scope_type == 2:  # MONTHLY
            grid_from, grid_to, solar, backup, self_help = 16.75, 13.56, 86.58, 77.39, 79
        elif scope_type == 3:  # ANNUAL
            grid_from, grid_to, solar, backup, self_help = 33.19, 77.77, 401.55, 301.53, 89
        else:  # TOTAL (scope 0)
            grid_from, grid_to, solar, backup, self_help = 33.19, 77.77, 401.55, 301.53, 89
        # Eco metrics — coal ≈ solar × 0.265 kg/kWh; treeNum ≈ coal × 0.137.
        # Real cube uses similar coefficients (verified empirically against
        # the EU firmware's response for our system on 2026-06-04).
        coal = round(solar * 0.265, 3)
        tree_num = round(coal * 0.13744, 5)
        return {
            "gridElectricity": grid_from,
            "gridElectricityFrom": grid_from,
            "gridElectricityTo": grid_to,
            "solarElectricity": solar,
            "solarDcElectricity": 0,
            "solarAcElectricity": 0,
            "generatorElectricity": 0,
            "evElectricity": 0,
            "nonBackUpElectricity": 0,
            "backUpElectricity": backup,
            "batteryChargeElectricity": 0,
            "batteryDischargeElectricity": 0,
            "selfHelpRate": self_help,
            "treeNum": tree_num,
            "coal": coal,
            "backupLoadsMode": 1,
            "hasValue": 1,
        }


# Two parallel lookups — devId for most endpoints, sgSn for homeDeviceInfo.
_DEFAULT = DeviceState()
DEVICES_BY_DEVID: dict[str, DeviceState] = {_DEFAULT.devId: _DEFAULT}
DEVICES_BY_SGSN: dict[str, DeviceState] = {_DEFAULT.sgSn: _DEFAULT}


def get_by_devid(dev_id: str) -> DeviceState | None:
    return DEVICES_BY_DEVID.get(dev_id)


def get_by_sgsn(sg_sn: str) -> DeviceState | None:
    return DEVICES_BY_SGSN.get(sg_sn)


def all_devices() -> list[DeviceState]:
    return list(DEVICES_BY_DEVID.values())
