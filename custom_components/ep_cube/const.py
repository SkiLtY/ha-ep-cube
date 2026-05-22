"""Constants for the EP Cube integration."""
from __future__ import annotations

DOMAIN = "ep_cube"

# Config-entry keys
CONF_BASE_URL = "base_url"           # e.g. https://monitoring-eu.epcube.com — data + auth host (mobile API)
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
DEFAULT_POLL_INTERVAL_SECONDS = 60   # real cloud polls at ~55s; we match

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
# decisions. Values are arbitrary as long as off < mid << peak.
SHIM_PRICE_OFF_PEAK = 0.01
SHIM_PRICE_MID_PEAK = 0.20
SHIM_PRICE_PEAK = 1.00
