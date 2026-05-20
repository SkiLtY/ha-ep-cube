"""EP Cube cloud API client.

Speaks the real `monitoring-eu.epcube.com` / `cas-eu.epcube.com` contract
captured on 2026-05-20. See `<captures-private>/2026-05-20-contract-extract.md` for the
wire reference. The mock at `mock_server/` mirrors this shape, so this client
works against either.

DeviceStatus field names are preserved from the prior implementation so
sensor.py is unchanged — the wire-format coercion (kW string → W float,
batterySoc int → soc_pct float, per-mode reserve selection) happens inside
`get_status()`. The shim layer (services.py) calls the typed setters
(`set_self_consumption`, `set_backup`, `set_tou_schedule`) directly.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    EP_CUBE_PACK_KWH,
    OPERATING_MODE_BACKUP,
    OPERATING_MODE_SELF_CONSUMPTION,
    OPERATING_MODE_TOU,
    WORK_STATUS_TO_OPERATING_MODE,
)

_LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------
class EPCubeError(Exception):
    """Base exception for EP Cube API errors."""


class AuthError(EPCubeError):
    """Authentication failed, cookie rejected, or session expired."""


class SessionExpiredError(AuthError):
    """Cloud session no longer valid — user must re-paste JSESSIONID."""


class RateLimitError(EPCubeError):
    """Cloud API rate limit hit (HTTP 429)."""


class ServerError(EPCubeError):
    """Cloud returned 5xx."""


# ----------------------------------------------------------------------
# Data model exposed to coordinator / sensor.py — unchanged field names
# ----------------------------------------------------------------------
@dataclass(slots=True)
class DeviceStatus:
    soc_pct: float
    soc_kwh: float
    capacity_kwh: float
    battery_power_w: float   # derived: solar + grid - load; >0 charging, <0 discharging
    grid_power_w: float      # working assumption: >0 import, <0 export (unverified)
    solar_power_w: float
    load_power_w: float      # backUpPower + nonBackUpPower
    operating_mode: str      # "self_consumption" | "time_of_use" | "backup"
    reserve_soc_pct: float   # per-mode reserve matching current workStatus


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _kw_str_to_w(value: Any) -> float:
    """Coerce the cloud's kW-as-string values to float watts.

    Accepts ints, floats, strings of decimals; returns 0.0 for None/empty.
    Defensive against the cloud's inconsistent typing (some endpoints return
    int/float, most return string)."""
    if value is None or value == "":
        return 0.0
    return float(value) * 1000.0


def _kwh_str_to_float(value: Any) -> float:
    """Coerce a kWh-as-string value to a float (kWh, not Wh — sensor takes kWh)."""
    if value is None or value == "":
        return 0.0
    return float(value)


def _capacity_string_to_kwh(value: Any) -> float:
    """Parse `getDeviceList`'s `systemCapacity: "20.0kWh"` into float kWh.
    Tolerates absent units (some cloud variants drop the suffix)."""
    if value is None:
        return 0.0
    s = str(value).strip().lower().removesuffix("kwh").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


# ----------------------------------------------------------------------
# TOU payload builder — used by the shim
# ----------------------------------------------------------------------
def build_slot(start_hhmm: str, end_hhmm: str, price: float) -> str:
    """Build a single TOU slot wire-string."""
    return f"{start_hhmm}_{end_hhmm}_{price:.2f}"


def build_tou_payload(
    dev_id: str,
    *,
    weekday_off: list[tuple[str, str, float]] | None = None,
    weekday_mid: list[tuple[str, str, float]] | None = None,
    weekday_peak: list[tuple[str, str, float]] | None = None,
    weekend_off: list[tuple[str, str, float]] | None = None,
    weekend_mid: list[tuple[str, str, float]] | None = None,
    weekend_peak: list[tuple[str, str, float]] | None = None,
    allow_grid_charge: bool = True,
    weekday_days: list[int] | None = None,
    weekend_days: list[int] | None = None,
    dst_active: bool = False,
) -> dict[str, Any]:
    """Build the full 18-field `setTimOfUse` POST body.

    Each tier list is a sequence of `(start_hhmm, end_hhmm, price)` tuples.
    `weekday_days` defaults to `[1,2,3,4,5]` (Mon–Fri); `weekend_days` to
    `[6,7]` (Sat+Sun). The DST-active parallel arrays are left empty unless
    the caller takes responsibility for populating them — most overrides
    apply the same schedule to both DST states, which the cloud handles by
    only consulting the non-DST set when `dayLightSavingTime == False`.
    """
    def slots(entries: list[tuple[str, str, float]] | None) -> list[str]:
        return [build_slot(*e) for e in (entries or [])]

    return {
        "devId": dev_id,
        "workStatus": "2",  # setTimOfUse always activates TOU mode
        "allowChargingXiaGrid": 1 if allow_grid_charge else 0,
        "peakTimeList": slots(weekday_peak),
        "midPeakTimeList": slots(weekday_mid),
        "offPeakTimeList": slots(weekday_off),
        "activeWeek": weekday_days or [1, 2, 3, 4, 5],
        "peakTimeListNonWorkDay": slots(weekend_peak),
        "midPeakTimeListNonWorkDay": slots(weekend_mid),
        "offPeakTimeListNonWorkDay": slots(weekend_off),
        "activeWeekNonWorkDay": weekend_days or [6, 7],
        "dayLightSavingTime": dst_active,
        "dayLightPeakTimeList": [],
        "dayLightMidPeakTimeList": [],
        "dayLightOffPeakTimeList": [],
        "dayLightActiveWeek": weekday_days or [1, 2, 3, 4, 5],
        "dayLightPeakTimeListNonWorkDay": [],
        "dayLightMidPeakTimeListNonWorkDay": [],
        "dayLightOffPeakTimeListNonWorkDay": [],
        "dayLightActiveWeekNonWorkDay": weekend_days or [6, 7],
    }


# ----------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------
class EPCubeClient:
    """Async client for the EP Cube cloud.

    Uses an explicit `Cookie: JSESSIONID=...` header on every request rather
    than the aiohttp ClientSession's shared cookie jar — HA's shared session
    is used by many integrations, and we don't want to pollute its jar with
    our auth cookie (or be vulnerable to other integrations clobbering ours).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        auth_url: str | None = None,
        dev_id: str,
        sg_sn: str,
        session_cookie: str | None = None,
        capacity_kwh: float = 0.0,
        # Mock-only convenience for dev-mode auth bootstrap (skips captcha).
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._auth_url = (auth_url or base_url).rstrip("/")
        self._dev_id = dev_id
        self._sg_sn = sg_sn
        self._session_cookie = session_cookie
        self._capacity_kwh = capacity_kwh
        self._username = username
        self._password = password

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    @property
    def dev_id(self) -> str:
        return self._dev_id

    @property
    def sg_sn(self) -> str:
        return self._sg_sn

    @property
    def device_id(self) -> str:
        """Backwards-compat alias — services.py / sensor.py still reference this."""
        return self._dev_id

    @property
    def capacity_kwh(self) -> float:
        return self._capacity_kwh

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    async def authenticate(self) -> None:
        """Establish a valid session.

        Priority order:
        1. If `session_cookie` was supplied (real cloud path), assume it's
           valid — we'll detect rejection on first call and raise.
        2. Else if `username` + `password` were supplied (mock dev path),
           POST to /cas/login and capture the JSESSIONID from Set-Cookie.
        3. Else raise — no way to authenticate without one of those.

        After establishing the cookie, fetch deviceList to cache capacity
        (and to act as a liveness probe).
        """
        if self._session_cookie is None and (self._username and self._password):
            await self._dev_login(self._username, self._password)
        if self._session_cookie is None:
            raise AuthError(
                "no session_cookie configured — paste a JSESSIONID from the "
                "Canadian Solar portal, or supply username/password (mock only)"
            )
        await self._refresh_capacity()

    async def _dev_login(self, username: str, password: str) -> None:
        """Mock-only convenience login. Real cloud requires the slider captcha."""
        url = f"{self._auth_url}/cas/login"
        async with self._session.post(
            url,
            data={"username": username, "password": password},
            allow_redirects=False,
        ) as resp:
            cookie = resp.cookies.get("JSESSIONID")
            if cookie is None:
                raise AuthError(
                    f"dev login at {url} did not return JSESSIONID "
                    f"(status={resp.status})"
                )
            self._session_cookie = cookie.value
            _LOGGER.debug("dev login: acquired JSESSIONID=%s", self._session_cookie[:8] + "...")

    async def _refresh_capacity(self) -> None:
        """Pull capacity from getDeviceList — also acts as auth probe."""
        try:
            devices = await self.get_device_list()
        except EPCubeError as err:
            _LOGGER.warning("capacity probe failed (%s); keeping configured value %.1f kWh",
                            err, self._capacity_kwh)
            return
        for entry in devices:
            if str(entry.get("id")) == self._dev_id or entry.get("sgSn") == self._sg_sn:
                capacity = _capacity_string_to_kwh(entry.get("systemCapacity"))
                if capacity > 0:
                    self._capacity_kwh = capacity
                    _LOGGER.debug("capacity from deviceList: %.1f kWh", capacity)
                return

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get_status(self) -> DeviceStatus:
        """Merge a live `homeDeviceInfo` poll with the current `getSwitchMode`
        snapshot into a single DeviceStatus. Two HTTP calls per poll — fine at
        the 60s default cadence."""
        info, mode = await asyncio.gather(
            self._get(f"/v1/api/home/homeDeviceInfo?sgSn={self._sg_sn}"),
            self._get(f"/v1/api/home/getSwitchMode?devId={self._dev_id}"),
        )

        solar_w = _kw_str_to_w(info.get("solarPower"))
        backup_w = _kw_str_to_w(info.get("backUpPower"))
        nonbackup_w = _kw_str_to_w(info.get("nonBackUpPower"))
        load_w = backup_w + nonbackup_w
        grid_w = _kw_str_to_w(info.get("gridPower"))
        # battery_power: power conservation. Sign convention: >0 = charging.
        # gridPower >0 = import confirmed on real cloud 2026-05-20 (night-time
        # check: solar=0, battery=0, load=grid balanced the identity).
        battery_w = solar_w + grid_w - load_w

        # System capacity = batteryPackNum × per-pack kWh. homeDeviceInfo
        # carries the count; deviceList doesn't expose systemCapacity on
        # parallel-pack accounts. Fall back to the cached value if missing.
        pack_num = info.get("batteryPackNum")
        if isinstance(pack_num, int) and pack_num > 0:
            self._capacity_kwh = pack_num * EP_CUBE_PACK_KWH

        work_status = str(info.get("workStatus", mode.get("workStatus", "1")))
        operating_mode = WORK_STATUS_TO_OPERATING_MODE.get(work_status, "unknown")
        reserve = _reserve_for_mode(operating_mode, mode)

        return DeviceStatus(
            soc_pct=float(info.get("batterySoc", 0)),
            soc_kwh=_kwh_str_to_float(info.get("batteryCurrentElectricity")),
            capacity_kwh=self._capacity_kwh,
            battery_power_w=battery_w,
            grid_power_w=grid_w,
            solar_power_w=solar_w,
            load_power_w=load_w,
            operating_mode=operating_mode,
            reserve_soc_pct=reserve,
        )

    async def get_switch_mode(self) -> dict[str, Any]:
        """Raw getSwitchMode — used by the shim for baseline snapshot."""
        return await self._get(f"/v1/api/home/getSwitchMode?devId={self._dev_id}")

    async def get_tou_schedule(self) -> dict[str, Any]:
        """Backwards-compat alias for the shim — returns the full getSwitchMode
        payload, which contains all schedule fields as well as mode/reserve."""
        return await self.get_switch_mode()

    async def get_device_list(self) -> list[dict[str, Any]]:
        """Returns the array of devices on the account. Used by config-flow
        to populate the device picker and by authenticate() to probe capacity."""
        data = await self._get("/v1/api/home/deviceList")
        if isinstance(data, list):
            return data
        return []

    async def get_selling_config(self) -> dict[str, Any]:
        """Export configuration (sellingEnable, sellingPowerLimit, grid code).
        Useful for the shim to know whether forced export is even allowed."""
        return await self._get(f"/v1/api/home/getSellingConfig/{self._dev_id}")

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def set_self_consumption(self, reserve_soc_pct: int) -> None:
        """Switch to Self-Consumption (workStatus=1) and set the reserve floor."""
        await self._post(
            "/v1/api/home/setSelfConsumption",
            json={
                "devId": self._dev_id,
                "workStatus": "1",
                "selfConsumptioinReserveSoc": int(reserve_soc_pct),  # sic — typo verbatim
            },
        )

    async def set_backup(self, reserve_soc_pct: int) -> None:
        """Switch to Backup (workStatus=3) and set the reserve floor."""
        await self._post(
            "/v1/api/home/setBackUp",
            json={
                "devId": self._dev_id,
                "workStatus": "3",
                "backupPowerReserveSoc": int(reserve_soc_pct),
            },
        )

    async def set_tou_schedule(self, payload: dict[str, Any]) -> None:
        """Switch to TOU (workStatus=2) AND save the schedule in one POST.

        `payload` must be the full wire-shape dict — use `build_tou_payload()`
        to construct one. The cloud bundles mode-switch with schedule-save,
        so there is no separate "switch to TOU mode" endpoint.
        """
        # Ensure devId is present and workStatus is correct, in case caller forgot.
        body = {**payload, "devId": self._dev_id, "workStatus": "2"}
        await self._post("/v1/api/home/setTimOfUse", json=body)

    async def clear_tou_schedule(self) -> None:
        """Wipe all TOU slot lists. Does NOT change mode — pair with
        set_self_consumption() if you want to fully reset."""
        await self._get(f"/v1/api/home/clearTouMode?devId={self._dev_id}")

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    async def _get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def _post(self, path: str, *, json: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, json=json)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        if self._session_cookie is None:
            raise SessionExpiredError("no session cookie — call authenticate() first")

        url = f"{self._base_url}{path}"
        headers = {"Cookie": f"JSESSIONID={self._session_cookie}"}

        async with self._session.request(
            method, url, headers=headers, json=json, allow_redirects=False,
        ) as resp:
            # Session-expired detection: real cloud redirects to /cas/login when
            # the cookie no longer maps to a valid session.
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                if "cas" in location.lower() or "login" in location.lower():
                    raise SessionExpiredError(
                        f"redirected to {location!r} — JSESSIONID expired, "
                        "re-authenticate via config-flow reconfigure"
                    )
                raise EPCubeError(f"{method} {path} unexpected redirect → {location!r}")
            if resp.status == 401:
                raise AuthError(f"{method} {path} returned 401")
            if resp.status == 429:
                raise RateLimitError(f"{method} {path} rate-limited")
            if resp.status >= 500:
                raise ServerError(f"{method} {path} returned {resp.status}")
            if resp.status >= 400:
                body_text = await resp.text()
                raise EPCubeError(f"{method} {path} returned {resp.status}: {body_text}")

            body = await resp.json()

        # Unwrap the standard envelope: {timestamp, message, status, data}.
        # Some responses have no data field (write endpoints) — that's fine.
        if not isinstance(body, dict):
            return body
        envelope_status = body.get("status", 200)
        if envelope_status != 200:
            msg = body.get("message", "unknown error")
            raise EPCubeError(f"{method} {path} envelope error status={envelope_status}: {msg}")
        return body.get("data")


# ----------------------------------------------------------------------
# Helpers used by get_status to extract the per-mode reserve
# ----------------------------------------------------------------------
def _reserve_for_mode(mode: str, switch_mode: dict[str, Any]) -> float:
    """Return the active mode's reserve SoC. Real cloud carries three
    per-mode reserves; we expose only the one matching current workStatus."""
    if mode == OPERATING_MODE_SELF_CONSUMPTION:
        return float(switch_mode.get("selfConsumptioinReserveSoc", 0) or 0)
    if mode == OPERATING_MODE_BACKUP:
        return float(switch_mode.get("backupPowerReserveSoc", 0) or 0)
    if mode == OPERATING_MODE_TOU:
        # TOU doesn't have its own reserve field — the inverter follows the
        # tier prices. Surface the self-consumption reserve as a sane proxy
        # since that's what kicks in outside any TOU window.
        return float(switch_mode.get("selfConsumptioinReserveSoc", 0) or 0)
    return 0.0
