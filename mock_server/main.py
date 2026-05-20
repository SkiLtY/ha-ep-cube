"""Mock EP Cube cloud server.

Endpoint paths, payload shapes, and response envelopes match the real
`monitoring-eu.epcube.com` / `cas-eu.epcube.com` contract captured on
2026-05-20. See `<captures-private>/2026-05-20-contract-extract.md` (auth + status +
mode) and `<captures-private>/2026-05-20-tou-extract.md` (TOU) for the source of truth.

Auth is intentionally permissive — the mock accepts any credentials and any
`JSESSIONID` cookie value (or none at all). The real cloud's slider captcha
is out of scope here; we only need shape parity for integration dev.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .state import (
    DEVICES_BY_DEVID,
    DEVICES_BY_SGSN,
    all_devices,
    build_slot,
    get_by_devid,
    get_by_sgsn,
    _now_envelope_ts,
)

app = FastAPI(title="EP Cube Mock Cloud", version="0.1.0")

MOCK_SESSION = "mock-session"


def envelope(data: Any = None, *, status: int = 200, message: str = "Success") -> dict[str, Any]:
    """Wrap any response in the cloud's standard envelope."""
    body: dict[str, Any] = {
        "timestamp": _now_envelope_ts(),
        "message": message,
        "status": status,
    }
    if data is not None:
        body["data"] = data
    return body


def _require_devid(dev_id: str):
    dev = get_by_devid(dev_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"devId {dev_id!r} not found")
    return dev


def _require_sgsn(sg_sn: str):
    dev = get_by_sgsn(sg_sn)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"sgSn {sg_sn!r} not found")
    return dev


# ----------------------------------------------------------------------
# Auth — mimics the CAS shape just enough for cookie-based session handling
# ----------------------------------------------------------------------
@app.post("/cas/login")
async def cas_login(request: Request):
    """Mimic the real CAS form-login. Accepts any credentials. Returns a 302
    that sets the `JSESSIONID` cookie on the response — mirrors the real
    cloud's redirect-after-login behaviour so the integration's cookie-jar
    handling can be exercised end-to-end."""
    target = "/v1/api/login/cas?ticket=ST-mock"
    resp = RedirectResponse(url=target, status_code=302)
    resp.set_cookie("JSESSIONID", MOCK_SESSION, path="/")
    return resp


@app.get("/v1/api/login/cas")
async def cas_callback():
    """Final hop of the CAS redirect chain. Real cloud lands here with a
    `?ticket=...` param after the form POST, then redirects to /. We just
    return success — the cookie is already set."""
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie("JSESSIONID", MOCK_SESSION, path="/")
    return resp


# ----------------------------------------------------------------------
# Session / metadata
# ----------------------------------------------------------------------
@app.get("/v1/api/system/user/getLoginUser")
async def get_login_user():
    """Liveness probe — the real cloud's first authenticated XHR."""
    return envelope({
        "userId": "1",
        "userName": "mock-user",
        "nickName": "Mock User",
        "email": "mock@example.com",
        "userType": "1",
        "uuid": "mock-uuid",
    })


@app.get("/v1/api/common/getVersion")
async def get_version():
    return envelope("V3.5.0-mock")


# ----------------------------------------------------------------------
# Device discovery
# ----------------------------------------------------------------------
@app.get("/v1/api/home/deviceList")
async def home_device_list():
    return envelope([dev.to_device_list_entry() for dev in all_devices()])


@app.get("/v1/api/device/getDeviceList")
async def device_get_device_list(pageNum: int = 1, pageSize: int = 10):
    entries = [dev.to_device_list_entry() for dev in all_devices()]
    return envelope({
        "total": len(entries),
        "rows": entries,
        "pageNum": pageNum,
        "pageSize": pageSize,
    })


# ----------------------------------------------------------------------
# Live status — the primary poll
# ----------------------------------------------------------------------
@app.get("/v1/api/home/homeDeviceInfo")
async def home_device_info(sgSn: str):
    dev = _require_sgsn(sgSn)
    return envelope(dev.to_home_device_info())


# ----------------------------------------------------------------------
# Mode + TOU schedule read
# ----------------------------------------------------------------------
@app.get("/v1/api/home/getSwitchMode")
async def get_switch_mode(devId: str):
    dev = _require_devid(devId)
    return envelope(dev.to_switch_mode())


# ----------------------------------------------------------------------
# Mode + TOU schedule writes — three separate endpoints, each bundles the
# mode switch with that mode's reserve / schedule.
# ----------------------------------------------------------------------
@app.post("/v1/api/home/setSelfConsumption")
async def set_self_consumption(payload: dict[str, Any]):
    dev = _require_devid(str(payload.get("devId", "")))
    dev.workStatus = "1"
    if "selfConsumptioinReserveSoc" in payload:
        dev.selfConsumptioinReserveSoc = int(payload["selfConsumptioinReserveSoc"])
    return envelope()


@app.post("/v1/api/home/setBackUp")
async def set_back_up(payload: dict[str, Any]):
    dev = _require_devid(str(payload.get("devId", "")))
    dev.workStatus = "3"
    if "backupPowerReserveSoc" in payload:
        dev.backupPowerReserveSoc = int(payload["backupPowerReserveSoc"])
    return envelope()


@app.post("/v1/api/home/setTimOfUse")
async def set_tim_of_use(payload: dict[str, Any]):
    """Switch to TOU mode AND save the schedule in one round-trip — bundled,
    just like the real cloud. The `workStatus` in the payload is always "2"."""
    dev = _require_devid(str(payload.get("devId", "")))
    dev.workStatus = "2"
    if "allowChargingXiaGrid" in payload:
        # Accept int OR string on write; the real cloud's frontend sends int.
        dev.allowChargingXiaGrid = str(payload["allowChargingXiaGrid"])
    dev.tou.apply_wire(payload)
    return envelope()


@app.get("/v1/api/home/clearTouMode")
async def clear_tou_mode(devId: str | None = None):
    """Wipe the TOU schedule. Real cloud confirmed to expose this in the JS
    bundle but not exercised in capture; assumed to clear all slot lists and
    leave workStatus alone (caller switches mode separately if needed)."""
    if devId is not None:
        dev = _require_devid(devId)
        targets = [dev]
    else:
        targets = all_devices()
    for d in targets:
        d.tou.peakTimeList = []
        d.tou.midPeakTimeList = []
        d.tou.offPeakTimeList = []
        d.tou.peakTimeListNonWorkDay = []
        d.tou.midPeakTimeListNonWorkDay = []
        d.tou.offPeakTimeListNonWorkDay = []
        d.tou.dayLightPeakTimeList = []
        d.tou.dayLightMidPeakTimeList = []
        d.tou.dayLightOffPeakTimeList = []
        d.tou.dayLightPeakTimeListNonWorkDay = []
        d.tou.dayLightMidPeakTimeListNonWorkDay = []
        d.tou.dayLightOffPeakTimeListNonWorkDay = []
    return envelope()


# ----------------------------------------------------------------------
# Selling / export config
# ----------------------------------------------------------------------
@app.get("/v1/api/home/getSellingConfig/{devId}")
async def get_selling_config(devId: str):
    dev = _require_devid(devId)
    return envelope(dev.to_selling_config())


# ----------------------------------------------------------------------
# Dev-only endpoints — inject state for tests, inspect what was written
# ----------------------------------------------------------------------
@app.post("/__sim__/device/{devId}/state")
async def sim_inject_state(devId: str, patch: dict[str, Any]):
    """Patch arbitrary scalar fields on the DeviceState. Useful for forcing
    a battery SoC / solar / load value in tests. TOU schedule edits should
    go through `setTimOfUse` so they exercise the real code path."""
    dev = _require_devid(devId)
    applied: dict[str, Any] = {}
    for key, value in patch.items():
        if hasattr(dev, key) and key != "tou":
            setattr(dev, key, value)
            applied[key] = value
    return envelope({"applied": applied})


@app.get("/__sim__/device/{devId}/tou-current")
async def sim_inspect_tou(devId: str):
    """Return the raw TOU schedule as the cloud would render it on read.
    Tests use this to verify the shim's writes landed correctly without
    going through `getSwitchMode`'s full envelope + mode wrapper."""
    dev = _require_devid(devId)
    return envelope(dev.tou.to_wire())


@app.post("/__sim__/device/{devId}/tou-set-slots")
async def sim_set_slots_quick(devId: str, payload: dict[str, Any]):
    """Test helper for building a TOU schedule without spelling out all 16
    arrays. Pass `{"weekday": {"offPeak": [["00:30","04:30",0.05], ...],
    "midPeak": [...], "peak": [...]}, "weekend": {...}}`. Lists are tuples
    of (start, end, price). Empty/missing keys leave that tier empty."""
    dev = _require_devid(devId)
    weekday = payload.get("weekday", {})
    weekend = payload.get("weekend", {})

    def slots(profile: dict[str, Any], key: str) -> list[str]:
        return [build_slot(*entry) for entry in profile.get(key, [])]

    dev.tou.offPeakTimeList = slots(weekday, "offPeak")
    dev.tou.midPeakTimeList = slots(weekday, "midPeak")
    dev.tou.peakTimeList = slots(weekday, "peak")
    dev.tou.offPeakTimeListNonWorkDay = slots(weekend, "offPeak")
    dev.tou.midPeakTimeListNonWorkDay = slots(weekend, "midPeak")
    dev.tou.peakTimeListNonWorkDay = slots(weekend, "peak")
    return envelope(dev.tou.to_wire())


@app.post("/__sim__/device/{devId}/reset")
async def sim_reset(devId: str):
    """Reset the device to factory defaults — handy for test isolation."""
    if devId not in DEVICES_BY_DEVID:
        raise HTTPException(status_code=404, detail=f"devId {devId!r} not found")
    from .state import DeviceState  # local import keeps test helper out of hot path
    fresh = DeviceState(devId=devId, sgSn=DEVICES_BY_DEVID[devId].sgSn)
    DEVICES_BY_DEVID[devId] = fresh
    DEVICES_BY_SGSN[fresh.sgSn] = fresh
    return envelope({"reset": devId})
