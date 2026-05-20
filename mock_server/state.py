"""In-memory device state for the mock EP Cube cloud.

Shapes match the real `monitoring-eu.epcube.com` contract captured on
2026-05-20 — see `<captures-private>/2026-05-20-contract-extract.md` (status + auth)
and `<captures-private>/2026-05-20-tou-extract.md` (TOU). Power values are stored as
floats in kW internally for sim convenience and rendered as decimal strings
on output to match the wire format. Schedule slots are stored as the
wire-format `"HH:MM_HH:MM_price"` strings directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_envelope_ts() -> str:
    """`YYYY-MM-DD HH:MM:SS` (UTC) — the cloud's envelope timestamp format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _kw(value: float) -> str:
    """Render a kW value the way the cloud does — 2 decimal places, as a string."""
    return f"{value:.2f}"


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
    backUpElectricity: float = 1.26
    nonBackUpElectricity: float = 0.00
    gridElectricity: float = 0.85
    generatorElectricity: float = 0.00
    evElectricity: float = 0.00

    # Mode + reserves (workStatus: "1"=self_consumption, "2"=tou, "3"=backup)
    workStatus: str = "1"
    selfConsumptioinReserveSoc: int = 10  # sic — typo preserved
    backupPowerReserveSoc: int = 100
    evChargerReserveSoc: int = 50
    allowChargingXiaGrid: str = "1"  # string on read, int on write — see TOU extract

    # Schedule
    tou: TouSchedule = field(default_factory=_default_tou)
    touType: int = 0
    weatherWatch: str = "0"
    onlySave: str = "0"

    # Selling / export config (getSellingConfig)
    sellingEnable: int = 1
    sellingPowerLimit: float = 7.30  # kW
    gridImportPowerMax: float = 7.30

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
        """Match `GET /v1/api/home/homeDeviceInfo?sgSn=...` response shape."""
        return {
            "devId": self.devId,
            "status": "1",
            "workStatus": self.workStatus,
            "batterySoc": self.batterySoc,
            "batteryCurrentElectricity": _kw(self.batteryCurrentElectricity),
            "gridPowerFailureNum": 0,
            "offGridPowerSupplyTime": 0,
            "gridPower": _kw(self.gridPower),
            "gridElectricity": _kw(self.gridElectricity),
            "solarPower": _kw(self.solarPower),
            "solarElectricity": _kw(self.solarElectricity),
            "generatorPower": _kw(self.generatorPower),
            "generatorElectricity": _kw(self.generatorElectricity),
            "evPower": _kw(self.evPower),
            "evElectricity": _kw(self.evElectricity),
            "nonBackUpPower": _kw(self.nonBackUpPower),
            "nonBackUpElectricity": _kw(self.nonBackUpElectricity),
            "backUpPower": _kw(self.backUpPower),
            "backUpElectricity": _kw(self.backUpElectricity),
            "selfHelpRate": "0.00",
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
            "gridTotalPower": _kw(self.gridPower),
            "gridHalfPower": "0.00",
            "solarFlow": _kw(self.solarPower),
            "solarAcPower": "0.00",
            "solarDcPower": _kw(self.solarPower),
            "generatorFlowPower": _kw(self.generatorPower),
            "evFlowPower": _kw(self.evPower),
            "nonBackUpFlowPower": _kw(self.nonBackUpPower),
            "backUpFlowPower": _kw(self.backUpPower),
            "backupLoadsMode": 1,
            "batteryPackNum": self.batteryPackNum,
            "devType": 1,
            "winterProtect": 85,
            "winterMode": 0,
            "off_ON_Grid_Hint": "Mock cloud — current behaviour explanation goes here.",
        }

    def to_switch_mode(self) -> dict[str, Any]:
        """Match `GET /v1/api/home/getSwitchMode?devId=...` response shape."""
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
        """Match one entry from `GET /v1/api/home/deviceList`. Extensive metadata."""
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

    def to_selling_config(self) -> dict[str, Any]:
        """Match `GET /v1/api/home/getSellingConfig/{devId}` response shape."""
        return {
            "sellingEnable": self.sellingEnable,
            "sellingPowerLimit": self.sellingPowerLimit,
            "gridImportPowerMax": self.gridImportPowerMax,
            "gridStandard": 11,
            "gridStandardName": "EREC G99 Issue 1 Amendment 9(2022)",
            "gridStandardPowerMinValue": 0.00,
            "gridStandardPowerMaxValue": 27.60,
            "g100EREC": 85,
            "g100AcOutputLimit": 7.30,
            "acExportPowerLimitEnabled": 1,
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
