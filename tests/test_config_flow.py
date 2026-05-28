"""Config flow tests — user step, region routing, captcha errors, migration."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow

from custom_components.ep_cube.captcha import CaptchaSolveError, LoginError
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
    REGION_OTHER,
    REGION_US,
)


@pytest.fixture
def patch_captcha():
    """Patch captcha.login to return a fake bearer token without touching the network."""
    with patch(
        "custom_components.ep_cube.config_flow.captcha_login",
        new=AsyncMock(return_value="fresh-token"),
    ) as mock:
        yield mock


@pytest.fixture
def patch_device_list(device_list):
    """Patch EPCubeClient.get_device_list to return the canonical fixture."""
    with patch(
        "custom_components.ep_cube.config_flow.EPCubeClient.get_device_list",
        new=AsyncMock(return_value=device_list),
    ) as mock:
        yield mock


# ----------------------------------------------------------------------
# Form rendering + happy path
# ----------------------------------------------------------------------
class TestUserStep:
    async def test_form_shown_on_first_load(self, hass):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] is None or result["errors"] == {}

    async def test_eu_region_creates_entry(self, hass, patch_captcha, patch_device_list):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "user@example.com",
                CONF_PASSWORD: "hunter2",
                CONF_REGION: REGION_EU,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        entry = result["data"]
        assert entry[CONF_REGION] == REGION_EU
        assert entry[CONF_BASE_URL] == "https://monitoring-eu.epcube.com"
        assert entry[CONF_API_PREFIX] == "/api"
        assert entry[CONF_BEARER_TOKEN] == "fresh-token"
        assert entry[CONF_DEV_ID] == "5613"
        assert entry[CONF_SG_SN] == "100100007001257120126"
        assert entry[CONF_CAPACITY_KWH] == 20.0

    async def test_us_region_uses_app_api_prefix(
        self, hass, patch_captcha, patch_device_list
    ):
        # US's distinctive trait: different host AND path prefix. Regression
        # against accidentally dropping the prefix routing.
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "u@e.com",
                CONF_PASSWORD: "x",
                CONF_REGION: REGION_US,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_REGION] == REGION_US
        assert result["data"][CONF_BASE_URL] == "https://epcube-monitoring.com"
        assert result["data"][CONF_API_PREFIX] == "/app-api"

    async def test_other_region_requires_base_url(self, hass, patch_captcha):
        # REGION_OTHER without base_url should re-render the form with an error.
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_USERNAME: "u@e.com",
                CONF_PASSWORD: "x",
                CONF_REGION: REGION_OTHER,
                CONF_BASE_URL: "",
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == {"base": "base_url_required"}
        # Captcha must NOT have been called — input validation happens first.
        patch_captcha.assert_not_called()


# ----------------------------------------------------------------------
# Error paths
# ----------------------------------------------------------------------
class TestErrorMapping:
    async def test_captcha_solve_failure(self, hass):
        with patch(
            "custom_components.ep_cube.config_flow.captcha_login",
            new=AsyncMock(side_effect=CaptchaSolveError("solver gave up")),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_USERNAME: "u@e.com",
                    CONF_PASSWORD: "x",
                    CONF_REGION: REGION_EU,
                },
            )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == {"base": "captcha_failed"}

    async def test_invalid_credentials(self, hass):
        with patch(
            "custom_components.ep_cube.config_flow.captcha_login",
            new=AsyncMock(side_effect=LoginError("bad password")),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_USERNAME: "u@e.com",
                    CONF_PASSWORD: "wrong",
                    CONF_REGION: REGION_EU,
                },
            )
        assert result["errors"] == {"base": "invalid_auth"}

    async def test_no_devices_in_account(self, hass, patch_captcha):
        with patch(
            "custom_components.ep_cube.config_flow.EPCubeClient.get_device_list",
            new=AsyncMock(return_value=[]),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_USERNAME: "u@e.com", CONF_PASSWORD: "x", CONF_REGION: REGION_EU},
            )
        assert result["errors"] == {"base": "no_devices"}

    async def test_multiple_devices_without_hint_is_ambiguous(self, hass, patch_captcha):
        # Without a dev_id / sg_sn hint and >1 device, can't auto-pick.
        with patch(
            "custom_components.ep_cube.config_flow.EPCubeClient.get_device_list",
            new=AsyncMock(return_value=[
                {"id": "5613", "sgSn": "AAA", "systemCapacity": "20.0kWh"},
                {"id": "9999", "sgSn": "BBB", "systemCapacity": "10.0kWh"},
            ]),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_USERNAME: "u@e.com", CONF_PASSWORD: "x", CONF_REGION: REGION_EU},
            )
        assert result["errors"] == {"base": "multiple_devices"}

    async def test_dev_id_hint_disambiguates(self, hass, patch_captcha):
        with patch(
            "custom_components.ep_cube.config_flow.EPCubeClient.get_device_list",
            new=AsyncMock(return_value=[
                {"id": "5613", "sgSn": "AAA", "systemCapacity": "20.0kWh"},
                {"id": "9999", "sgSn": "BBB", "systemCapacity": "10.0kWh"},
            ]),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_USERNAME: "u@e.com", CONF_PASSWORD: "x", CONF_REGION: REGION_EU,
                    CONF_DEV_ID: "9999",
                },
            )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_DEV_ID] == "9999"
        assert result["data"][CONF_CAPACITY_KWH] == 10.0


# ----------------------------------------------------------------------
# Unique-id guard — don't double-add the same device.
# ----------------------------------------------------------------------
class TestUniqueId:
    async def test_duplicate_dev_id_aborts(
        self, hass, patch_captcha, patch_device_list, mock_config_entry
    ):
        existing = mock_config_entry()
        existing.add_to_hass(hass)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "u@e.com", CONF_PASSWORD: "x", CONF_REGION: REGION_EU},
        )
        assert result["type"] == data_entry_flow.FlowResultType.ABORT
        assert result["reason"] == "already_configured"


# ----------------------------------------------------------------------
# async_migrate_entry: v4 (pre-region) → v5 (region + api_prefix)
# ----------------------------------------------------------------------
class TestMigration:
    async def test_v4_eu_entry_infers_region(self, hass):
        from pytest_homeassistant_custom_component.common import MockConfigEntry
        from custom_components.ep_cube import async_migrate_entry

        entry = MockConfigEntry(
            domain=DOMAIN,
            version=4,
            data={
                CONF_BASE_URL: "https://monitoring-eu.epcube.com",
                CONF_USERNAME: "u@e.com",
                CONF_PASSWORD: "x",
                CONF_DEV_ID: "5613",
                CONF_SG_SN: "100100007001257120126",
                CONF_BEARER_TOKEN: "old-token",
                CONF_CAPACITY_KWH: 20.0,
            },
        )
        entry.add_to_hass(hass)
        assert await async_migrate_entry(hass, entry) is True
        assert entry.version == 5
        assert entry.data[CONF_REGION] == REGION_EU
        assert entry.data[CONF_API_PREFIX] == "/api"

    async def test_v4_us_entry_infers_app_api_prefix(self, hass):
        from pytest_homeassistant_custom_component.common import MockConfigEntry
        from custom_components.ep_cube import async_migrate_entry

        entry = MockConfigEntry(
            domain=DOMAIN,
            version=4,
            data={
                CONF_BASE_URL: "https://epcube-monitoring.com",
                CONF_USERNAME: "u@e.com",
                CONF_PASSWORD: "x",
                CONF_DEV_ID: "5613",
                CONF_SG_SN: "100100007001257120126",
                CONF_BEARER_TOKEN: "old-token",
                CONF_CAPACITY_KWH: 20.0,
            },
        )
        entry.add_to_hass(hass)
        await async_migrate_entry(hass, entry)
        assert entry.data[CONF_REGION] == REGION_US
        assert entry.data[CONF_API_PREFIX] == "/app-api"

    async def test_v4_unknown_host_falls_back_to_other(self, hass):
        from pytest_homeassistant_custom_component.common import MockConfigEntry
        from custom_components.ep_cube import async_migrate_entry

        entry = MockConfigEntry(
            domain=DOMAIN,
            version=4,
            data={
                CONF_BASE_URL: "https://mock.invalid",  # dev fork / mock server
                CONF_DEV_ID: "5613",
                CONF_SG_SN: "x",
            },
        )
        entry.add_to_hass(hass)
        await async_migrate_entry(hass, entry)
        assert entry.data[CONF_REGION] == REGION_OTHER
        assert entry.data[CONF_API_PREFIX] == "/api"

    async def test_v5_entry_passes_through_unchanged(self, hass, mock_config_entry):
        from custom_components.ep_cube import async_migrate_entry
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        assert await async_migrate_entry(hass, entry) is True
        assert entry.version == 5
