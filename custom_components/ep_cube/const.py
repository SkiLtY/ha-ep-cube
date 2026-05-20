"""Constants for the EP Cube integration."""
from __future__ import annotations

DOMAIN = "ep_cube"

# Config-entry keys
CONF_BASE_URL = "base_url"           # e.g. https://monitoring-eu.epcube.com — data host
CONF_AUTH_URL = "auth_url"           # e.g. https://cas-eu.epcube.com — auth host (optional; defaults to base_url)
CONF_DEV_ID = "dev_id"               # e.g. "5613" — small int, used by most endpoints
CONF_SG_SN = "sg_sn"                 # 21-digit serial, used by homeDeviceInfo
CONF_SESSION_COOKIE = "session_cookie"  # JSESSIONID value pasted from browser (real cloud)
CONF_USERNAME = "username"           # mock-only dev convenience (skips captcha)
CONF_PASSWORD = "password"           # mock-only dev convenience
CONF_CAPACITY_KWH = "capacity_kwh"   # cached at config-flow time from getDeviceList

DEFAULT_BASE_URL = "http://mock:8765"
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

# Predbat publishes its planned window to entities of the form
#   sensor.{PREDBAT_ENTITY_PREFIX}_{PREDBAT_INVERTER_TYPE}_{index}_{name}
# These must match the values configured in predbat_config/apps.yaml
# (`inverter_type` + `ge_inverter` index). Single-device assumption; multi-device
# support is deferred to Phase 4.
PREDBAT_ENTITY_PREFIX = "predbat"
PREDBAT_INVERTER_TYPE = "EP_CUBE"
PREDBAT_INVERTER_INDEX = 0

# Predbat writes "23:59:00" / "23:59:00" to a window pair to mean "no plan".
PREDBAT_NO_PLAN_TIME = "23:59:00"

# Synthetic prices for the shim's tier translation. The real cloud's TOU is
# price-tier based — we tell it "off-peak" / "mid-peak" / "peak" with prices,
# and the inverter optimises. Predbat's mental model is "charge/hold/discharge"
# with explicit slots; the shim maps that onto tiers using these synthetic
# prices, spaced wide enough that there's no ambiguity in the inverter's
# decisions. Values are arbitrary as long as off < mid << peak.
SHIM_PRICE_OFF_PEAK = 0.01
SHIM_PRICE_MID_PEAK = 0.20
SHIM_PRICE_PEAK = 1.00
