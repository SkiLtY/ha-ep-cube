"""Mock EP Cube cloud server.

Endpoint shapes are working assumptions to be reconciled when real cloud API
traffic is captured from the EP Cube mobile app.

Test credentials: any username/password is accepted. Token is "mock-token".
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .state import DEVICES

app = FastAPI(title="EP Cube Mock Cloud", version="0.0.1")

VALID_TOKEN = "mock-token"


class LoginRequest(BaseModel):
    username: str
    password: str


class OperatingModeRequest(BaseModel):
    mode: str
    reserve_soc_pct: float | None = None


def _check_token(authorization: str | None) -> None:
    if authorization != f"Bearer {VALID_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid token")


@app.post("/api/v1/auth/login")
async def login(req: LoginRequest) -> dict[str, str]:
    if not req.username or not req.password:
        raise HTTPException(status_code=401, detail="missing credentials")
    return {"token": VALID_TOKEN}


@app.get("/api/v1/device/{device_id}/status")
async def get_status(device_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _check_token(authorization)
    if device_id not in DEVICES:
        raise HTTPException(status_code=404, detail="device not found")
    return DEVICES[device_id].to_status_dict()


@app.post("/api/v1/device/{device_id}/operating-mode")
async def set_operating_mode(
    device_id: str,
    req: OperatingModeRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _check_token(authorization)
    if device_id not in DEVICES:
        raise HTTPException(status_code=404, detail="device not found")
    DEVICES[device_id].operating_mode = req.mode
    if req.reserve_soc_pct is not None:
        DEVICES[device_id].reserve_soc_pct = req.reserve_soc_pct
    return {"status": "ok"}


@app.post("/api/v1/device/{device_id}/tou-schedule")
async def set_tou_schedule(
    device_id: str,
    schedule: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _check_token(authorization)
    if device_id not in DEVICES:
        raise HTTPException(status_code=404, detail="device not found")
    DEVICES[device_id].tou_schedule = schedule
    return {"status": "ok"}


# Dev-only endpoints — inject state for testing
@app.post("/__sim__/device/{device_id}/state")
async def sim_inject_state(device_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    if device_id not in DEVICES:
        raise HTTPException(status_code=404, detail="device not found")
    state = DEVICES[device_id]
    for key, value in patch.items():
        if hasattr(state, key):
            setattr(state, key, value)
    return state.to_status_dict()
