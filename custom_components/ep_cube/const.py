"""Constants for the EP Cube integration."""
from __future__ import annotations

DOMAIN = "ep_cube"

CONF_BASE_URL = "base_url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"

DEFAULT_BASE_URL = "http://mock:8765"
DEFAULT_POLL_INTERVAL_SECONDS = 30

OPERATING_MODE_SELF_CONSUMPTION = "self_consumption"
OPERATING_MODE_TOU = "time_of_use"
OPERATING_MODE_BACKUP = "backup"
