"""Constants for the EP Cube integration."""
from __future__ import annotations

DOMAIN = "ep_cube"

# Config-entry keys
CONF_BASE_URL = "base_url"           # e.g. https://monitoring-eu.epcube.com — data + auth host (mobile API)
CONF_API_PREFIX = "api_prefix"       # path prefix the cloud expects; "/api" for EU/JP, "/app-api" for US
CONF_REGION = "region"               # one of REGIONS keys (EU/US/JP) or REGION_OTHER for a custom endpoint
CONF_DEV_ID = "dev_id"               # e.g. "5613" — small int, used by most endpoints
CONF_SG_SN = "sg_sn"                 # 21-digit serial, used by homeDeviceInfo
CONF_USERNAME = "username"           # email used to log into EP Cube mobile app
CONF_PASSWORD = "password"           # password used to log into EP Cube mobile app (stored encrypted at rest by HA)
CONF_BEARER_TOKEN = "bearer_token"   # cached Bearer token; refreshed in-memory via captcha login on 403
CONF_CAPACITY_KWH = "capacity_kwh"   # cached fallback; primary source is batteryPackNum × EP_CUBE_PACK_KWH

# Per-pack capacity. EP Cube is a single-module product line; all packs are
# 5.0 kWh. System capacity = batteryPackNum × this. Sourced from the real-cloud
# homeDeviceInfo response which exposes batteryPackNum but not systemCapacity.
EP_CUBE_PACK_KWH = 5.0

DEFAULT_BASE_URL = "https://monitoring-eu.epcube.com"
DEFAULT_API_PREFIX = "/api"
DEFAULT_POLL_INTERVAL_SECONDS = 60   # real cloud polls at ~55s; we match

# Region endpoints — cross-referenced against Bobsilvio/epcube/const.py
# (BASE_URLS dict). The US region notably uses a different hostname AND a
# different path prefix ("/app-api" instead of "/api"), so both pieces are
# stored and threaded through api.py + captcha.py.
REGION_EU = "EU"
REGION_US = "US"
REGION_JP = "JP"
REGION_OTHER = "other"

REGIONS: dict[str, dict[str, str]] = {
    REGION_EU: {"base_url": "https://monitoring-eu.epcube.com", "api_prefix": "/api"},
    REGION_US: {"base_url": "https://epcube-monitoring.com", "api_prefix": "/app-api"},
    REGION_JP: {"base_url": "https://monitoring-jp.epcube.com", "api_prefix": "/api"},
}

DEFAULT_REGION = REGION_EU

# Operating-mode names. Internal API stays in these strings; mapping to
# the cloud's "workStatus" numeric codes happens in api.py.
OPERATING_MODE_SELF_CONSUMPTION = "self_consumption"
OPERATING_MODE_TOU = "time_of_use"
OPERATING_MODE_BACKUP = "backup"

# Wire-side workStatus codes (strings on the wire).
WORK_STATUS_SELF_CONSUMPTION = "1"
WORK_STATUS_TOU = "2"
WORK_STATUS_BACKUP = "3"

OPERATING_MODE_TO_WORK_STATUS: dict[str, str] = {
    OPERATING_MODE_SELF_CONSUMPTION: WORK_STATUS_SELF_CONSUMPTION,
    OPERATING_MODE_TOU: WORK_STATUS_TOU,
    OPERATING_MODE_BACKUP: WORK_STATUS_BACKUP,
}
WORK_STATUS_TO_OPERATING_MODE: dict[str, str] = {
    v: k for k, v in OPERATING_MODE_TO_WORK_STATUS.items()
}

# Synthetic prices for the shim's tier translation. The real cloud's TOU is
# price-tier based — we tell it "off-peak" / "mid-peak" / "peak" with prices,
# and the inverter optimises. Predbat's mental model is "charge/hold/discharge"
# with explicit slots; the shim maps that onto tiers using these synthetic
# prices, spaced wide enough that there's no ambiguity in the inverter's
# decisions.
#
# Values chosen to be obviously synthetic: above Agile's 100p/kWh daily cap so
# no realistic UK tariff hits them, and the repeating-digit pattern
# (2.22/3.33/4.44) makes shim slots visually identifiable if a user spots one
# in the EP Cube mobile app's schedule view.
#
# Legacy values were 0.01 / 0.20 / 1.00 — replaced in v0.6.3 because 1.00
# (peak) collided with realistic fixed-tariff peak prices, causing the
# strip-shim-slots logic to silently drop genuine user slots from the editor
# card's hydrated view. The v0.6.3 migration window kept the legacy values
# in the strip set for one release; v0.7 dropped them since any in-flight
# pre-v0.6.3 overrides had rotated out by then.
SHIM_PRICE_OFF_PEAK = 2.22
SHIM_PRICE_MID_PEAK = 3.33
SHIM_PRICE_PEAK = 4.44

