"""Shared pytest fixtures.

Uses `pytest-homeassistant-custom-component` for the HA-aware tests (config
flow, coordinator/entity setup). Pure-Python tests against api.py / shim /
predbat_state don't need any HA fixtures and stay framework-agnostic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# `enable_custom_integrations` is the canonical fixture from
# pytest-homeassistant-custom-component — it tells HA to look in
# custom_components/ rather than only the core integrations dir.
pytest_plugins = ["pytest_homeassistant_custom_component"]


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# Auto-enable custom integrations for every test (avoids a deprecation
# warning when individual tests forget the fixture).
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: ARG001
    """Activate HA's custom_components discovery for every test."""
    yield


# ----------------------------------------------------------------------
# JSON fixture loaders — used by api/transport + setup tests.
# ----------------------------------------------------------------------
@pytest.fixture
def home_device_info() -> dict[str, Any]:
    """Canonical /api/device/homeDeviceInfo payload (EU mobile shape)."""
    return _load("home_device_info.json")


@pytest.fixture
def get_switch_mode() -> dict[str, Any]:
    """Canonical /api/device/getSwitchMode payload."""
    return _load("get_switch_mode.json")


@pytest.fixture
def device_list() -> list[dict[str, Any]]:
    """Canonical /api/device/deviceList payload."""
    return _load("device_list.json")


# ----------------------------------------------------------------------
# A pre-built fake client for tests that exercise sensor/coordinator/shim
# code without mocking HTTP. Returns a MagicMock with AsyncMock methods.
# ----------------------------------------------------------------------
@pytest.fixture
def fake_client(get_switch_mode, device_list):
    from custom_components.ep_cube.api import DeviceStatus

    client = MagicMock()
    client.dev_id = "5613"
    client.sg_sn = "100100007001257120126"
    client.device_id = "5613"
    client.capacity_kwh = 20.0
    client.bearer_token = "fake-token"

    client.authenticate = AsyncMock(return_value=None)
    client.get_device_list = AsyncMock(return_value=device_list)
    client.get_switch_mode = AsyncMock(return_value=dict(get_switch_mode))
    client.switch_mode = AsyncMock(return_value=dict(get_switch_mode))

    client.get_status = AsyncMock(
        return_value=DeviceStatus(
            soc_pct=55.0,
            soc_kwh=11.0,
            capacity_kwh=20.0,
            battery_power_w=400.0,
            grid_power_w=0.0,
            solar_power_w=1200.0,
            load_power_w=800.0,
            operating_mode="self_consumption",
            reserve_soc_pct=20.0,
            allow_grid_charge=True,
            self_consumption_reserve_pct=20.0,
            backup_reserve_pct=100.0,
            dst_active=False,
            solar_today_kwh=0.35,
            grid_today_kwh=0.85,
            backup_today_kwh=1.26,
            nonbackup_today_kwh=0.0,
            solar_dc_today_kwh=0.36,
            solar_ac_today_kwh=0.34,
            self_consumption_pct=65.0,
            winter_protect_pct=85.0,
            earning_yesterday=1.23,
            grid_outage_count=2,
            off_grid_seconds=1837,
            battery_charge_today_kwh=0.0,
            battery_discharge_today_kwh=0.0,
        )
    )
    return client


# ----------------------------------------------------------------------
# Config entry factory used by integration-shape tests (config_flow, setup).
# ----------------------------------------------------------------------
@pytest.fixture
def mock_config_entry():
    """Return a builder that materialises a MockConfigEntry on demand."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.ep_cube.const import (
        CONF_API_PREFIX,
        CONF_BASE_URL,
        CONF_BEARER_TOKEN,
        CONF_CAPACITY_KWH,
        CONF_DEV_ID,
        CONF_PASSWORD,
        CONF_REGION,
        CONF_SG_SN,
        CONF_USERNAME,
        DOMAIN,
        REGION_EU,
    )

    def _make(**overrides: Any) -> MockConfigEntry:
        data = {
            CONF_REGION: REGION_EU,
            CONF_BASE_URL: "https://monitoring-eu.epcube.com",
            CONF_API_PREFIX: "/api",
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "hunter2",
            CONF_BEARER_TOKEN: "fake-bearer-token",
            CONF_DEV_ID: "5613",
            CONF_SG_SN: "100100007001257120126",
            CONF_CAPACITY_KWH: 20.0,
        }
        data.update(overrides)
        return MockConfigEntry(
            domain=DOMAIN,
            data=data,
            unique_id=data[CONF_DEV_ID],
            version=5,
        )

    return _make
