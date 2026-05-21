"""Mock EP Cube cloud server — mobile-app surface.

Mirrors the contract that `custom_components/ep_cube/api.py` and
`custom_components/ep_cube/captcha.py` speak after the Phase 3.2 refactor:

- `/api/open/common/captcha/get` + `/captcha/check` + `/login` for the
  4-POST captcha-solver login flow (see captcha.py docstring).
- `/api/device/deviceList` / `homeDeviceInfo` / `getSwitchMode` /
  `switchMode` for the polling + write paths.

Auth is intentionally permissive: any non-empty Bearer token is accepted,
and the captcha images are tiny PIL-friendly placeholders that the
template-matching solver can run on without hanging. The encrypted
`pointJson` and `captchaVerification` are NOT validated — the mock cares
about *shape parity* with the real cloud, not cryptographic correctness.

Dev-only `/__sim__/*` endpoints (state injection + inspection) are kept
from the original web-portal mock.
"""
from __future__ import annotations

import base64
import io
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from PIL import Image

from .state import (
    DEVICES_BY_DEVID,
    DEVICES_BY_SGSN,
    all_devices,
    build_slot,
    get_by_devid,
    get_by_sgsn,
    _now_envelope_ts,
)

app = FastAPI(title="EP Cube Mock Cloud", version="0.2.0")

# Issued by /api/open/common/login. The mock accepts ANY non-empty Bearer
# on /api/device/* so token-rotation tests aren't blocked by this value.
MOCK_BEARER_TOKEN = "mock-bearer-token"
# 16-byte ASCII — AES-128 key length, matches the real cloud's format.
MOCK_CAPTCHA_SECRET = "mockmockmockmock"
# Issued by /captcha/get, echoed back into /check + the verification blob.
MOCK_CAPTCHA_TOKEN = "mock-captcha-token"


# ----------------------------------------------------------------------
# Envelope
# ----------------------------------------------------------------------
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


def _require_bearer(authorization: str | None) -> None:
    """Reject /api/device/* calls without a Bearer header.

    The mock accepts any non-empty token rather than checking equality to
    MOCK_BEARER_TOKEN — that way reauth-callback tests can rotate tokens
    freely without us tracking them. Real cube returns 403 on bad token;
    we mirror that path."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=403, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=403, detail="empty bearer token")


# ----------------------------------------------------------------------
# Captcha image generation — tiny palette-mode PNGs the solver can run on
# ----------------------------------------------------------------------
def _make_placeholder_png(width: int, height: int, *, fill: int = 128) -> str:
    """Build a single-colour palette PNG and base64-encode it.

    Palette mode matches the real cube's image encoding — captcha.py
    deliberately reads palette indices rather than RGB, so this also
    exercises the same code path. The image is solid (no puzzle to find);
    the solver will pick *some* x and we accept whatever it sends."""
    img = Image.new("P", (width, height), color=fill)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# Generated once at import — bg ≥ puzzle in both dims so the solver does
# not return None and short-circuit the login flow.
_PLACEHOLDER_BG_PNG = _make_placeholder_png(80, 40, fill=200)
_PLACEHOLDER_PUZZLE_PNG = _make_placeholder_png(40, 40, fill=64)


# ----------------------------------------------------------------------
# Captcha + login — open endpoints, no Bearer required
# ----------------------------------------------------------------------
@app.post("/api/open/common/captcha/get")
async def captcha_get(payload: dict[str, Any]):
    """Serve a fresh captcha. `clientUid` is echoed in dev logs but not
    enforced — multiple parallel solves would collide on the real cloud's
    per-clientUid cache but the mock has no such cache."""
    return envelope({
        "repCode": "0000",
        "repData": {
            "secretKey": MOCK_CAPTCHA_SECRET,
            "token": MOCK_CAPTCHA_TOKEN,
            "originalImageBase64": _PLACEHOLDER_BG_PNG,
            "jigsawImageBase64": _PLACEHOLDER_PUZZLE_PNG,
        },
    })


@app.post("/api/open/common/captcha/check")
async def captcha_check(payload: dict[str, Any]):
    """Accept any encrypted pointJson. Real cube validates the AES-decoded
    {x,y} against the puzzle's true offset (±5 px); mock skips that —
    `repCode:"0000"` always."""
    return envelope({
        "repCode": "0000",
        "repData": {"result": True},
    })


@app.post("/api/open/common/login")
async def open_login(payload: dict[str, Any]):
    """Hand out a Bearer token. Any credentials accepted — the real cube
    validates `userName`/`password` against the CAS DB but for shape
    parity we only care that a token comes back."""
    return envelope({
        "token": MOCK_BEARER_TOKEN,
        "userId": "1",
        "userName": payload.get("userName", "mock-user"),
        "nickName": "Mock User",
    })


# ----------------------------------------------------------------------
# Device endpoints — Bearer-protected
# ----------------------------------------------------------------------
@app.get("/api/device/deviceList")
async def device_list(authorization: str | None = Header(default=None)):
    """List devices on this account. Returned as a JSON list (not paginated)
    matching api.py `get_device_list` expectations."""
    _require_bearer(authorization)
    return envelope([dev.to_device_list_entry() for dev in all_devices()])


@app.get("/api/device/homeDeviceInfo")
async def home_device_info(
    sgSn: str,
    dayMonthYearFormat: str | None = None,
    authorization: str | None = Header(default=None),
):
    """Live battery + power-flow snapshot. Keyed by sgSn."""
    _require_bearer(authorization)
    dev = _require_sgsn(sgSn)
    return envelope(dev.to_home_device_info())


@app.get("/api/device/getSwitchMode")
async def get_switch_mode(
    devId: str,
    authorization: str | None = Header(default=None),
):
    """Current mode + reserves + full TOU schedule."""
    _require_bearer(authorization)
    dev = _require_devid(devId)
    return envelope(dev.to_switch_mode())


@app.post("/api/device/switchMode")
async def switch_mode(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
):
    """Unified mode + TOU write. Replaces the web-portal trio
    (setSelfConsumption / setBackUp / setTimOfUse). Required fields
    (devId, workStatus, weatherWatch, onlySave, reserve SoCs, day arrays
    as list[str], native int touType, native bool dayLightSavingTime) are
    validated minimally — missing `weatherWatch` returns 500 to mirror
    the real cube's `"The parameter cannot be null ：weatherWatch"`."""
    _require_bearer(authorization)
    dev_id = str(payload.get("devId", ""))
    if not dev_id:
        raise HTTPException(status_code=500, detail="The parameter cannot be null ：devId")
    dev = _require_devid(dev_id)

    for required in ("workStatus", "weatherWatch", "onlySave"):
        if required not in payload:
            raise HTTPException(
                status_code=500,
                detail=f"The parameter cannot be null ：{required}",
            )

    dev.workStatus = str(payload["workStatus"])
    dev.weatherWatch = str(payload["weatherWatch"])
    dev.onlySave = str(payload["onlySave"])
    if "selfConsumptioinReserveSoc" in payload:
        dev.selfConsumptioinReserveSoc = int(payload["selfConsumptioinReserveSoc"])
    if "backupPowerReserveSoc" in payload:
        dev.backupPowerReserveSoc = int(payload["backupPowerReserveSoc"])
    if "allowChargingXiaGrid" in payload:
        dev.allowChargingXiaGrid = str(payload["allowChargingXiaGrid"])
    if "touType" in payload:
        dev.touType = int(payload["touType"])
    dev.tou.apply_wire(payload)
    return envelope()


# ----------------------------------------------------------------------
# Dev-only endpoints — inject state for tests, inspect what was written
# ----------------------------------------------------------------------
@app.post("/__sim__/device/{devId}/state")
async def sim_inject_state(devId: str, patch: dict[str, Any]):
    """Patch arbitrary scalar fields on the DeviceState. Useful for forcing
    a battery SoC / solar / load value in tests. TOU schedule edits should
    go through `switchMode` so they exercise the real code path."""
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
