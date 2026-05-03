"""EP Cube cloud API client.

Endpoint shapes are working assumptions to be reconciled against captured
traffic from the EP Cube mobile app once hardware arrives.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class EPCubeError(Exception):
    """Base exception for EP Cube API errors."""


class AuthError(EPCubeError):
    """Authentication failed or token rejected."""


class RateLimitError(EPCubeError):
    """Cloud API rate limit hit (HTTP 429)."""


class ServerError(EPCubeError):
    """Cloud returned 5xx."""


@dataclass(slots=True)
class DeviceStatus:
    soc_pct: float
    soc_kwh: float
    capacity_kwh: float
    battery_power_w: float
    grid_power_w: float
    solar_power_w: float
    load_power_w: float
    operating_mode: str
    reserve_soc_pct: float


class EPCubeClient:
    """Thin async client for the EP Cube cloud API.

    The shim layer wraps this to provide Predbat-shaped semantics.
    This class stays close to whatever the cloud API actually exposes.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        username: str,
        password: str,
        device_id: str,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._device_id = device_id
        self._token: str | None = None

    async def authenticate(self) -> None:
        async with self._session.post(
            f"{self._base_url}/api/v1/auth/login",
            json={"username": self._username, "password": self._password},
        ) as resp:
            if resp.status == 401:
                raise AuthError("invalid credentials")
            if resp.status >= 500:
                raise ServerError(f"auth returned {resp.status}")
            data = await resp.json()
            self._token = data["token"]

    async def get_status(self) -> DeviceStatus:
        data = await self._request("GET", f"/api/v1/device/{self._device_id}/status")
        return DeviceStatus(
            soc_pct=data["soc_pct"],
            soc_kwh=data["soc_kwh"],
            capacity_kwh=data["capacity_kwh"],
            battery_power_w=data["battery_power_w"],
            grid_power_w=data["grid_power_w"],
            solar_power_w=data["solar_power_w"],
            load_power_w=data["load_power_w"],
            operating_mode=data["operating_mode"],
            reserve_soc_pct=data["reserve_soc_pct"],
        )

    async def set_operating_mode(self, mode: str, reserve_soc_pct: float | None = None) -> None:
        body: dict[str, Any] = {"mode": mode}
        if reserve_soc_pct is not None:
            body["reserve_soc_pct"] = reserve_soc_pct
        await self._request("POST", f"/api/v1/device/{self._device_id}/operating-mode", json=body)

    async def set_tou_schedule(self, schedule: dict[str, Any]) -> None:
        await self._request(
            "POST",
            f"/api/v1/device/{self._device_id}/tou-schedule",
            json=schedule,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        _retried_auth: bool = False,
    ) -> dict[str, Any]:
        if self._token is None:
            await self.authenticate()

        headers = {"Authorization": f"Bearer {self._token}"}
        url = f"{self._base_url}{path}"

        async with self._session.request(method, url, headers=headers, json=json) as resp:
            if resp.status == 401 and not _retried_auth:
                _LOGGER.debug("token rejected, re-authenticating")
                self._token = None
                return await self._request(method, path, json=json, _retried_auth=True)
            if resp.status == 429:
                raise RateLimitError("rate limit hit")
            if resp.status >= 500:
                raise ServerError(f"{method} {path} returned {resp.status}")
            if resp.status >= 400:
                raise EPCubeError(f"{method} {path} returned {resp.status}: {await resp.text()}")
            return await resp.json()
