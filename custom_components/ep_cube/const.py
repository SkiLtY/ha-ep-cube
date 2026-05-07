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
