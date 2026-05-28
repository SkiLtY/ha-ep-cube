"""Integration setup tests: async_setup_entry wires up all platforms.

Patches EPCubeClient at the integration boundary so no HTTP fires, then
asserts the expected entity IDs land on the bus and their categories are
correctly tagged (CONFIG for writables, DIAGNOSTIC for duplicates).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from custom_components.ep_cube.const import DOMAIN


@pytest.fixture
async def setup_integration(hass, mock_config_entry, fake_client):
    """Set up the integration with a fake client and return the entry.

    Yields the entry, then unloads in teardown. Unload is required because
    the coordinator's `update_interval` schedules a recurring timer, and
    PHCC fails the test on any lingering timer.
    """
    entry = mock_config_entry()
    entry.add_to_hass(hass)

    # Patch the client constructor + the captcha-login reauth factory so
    # nothing reaches the network. The fake_client fixture already returns
    # a fully-stubbed client.
    with (
        patch(
            "custom_components.ep_cube.EPCubeClient",
            return_value=fake_client,
        ),
        patch(
            "custom_components.ep_cube.make_reauth_callback",
            return_value=AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry
        # Best-effort teardown. test_unload_entry_clears_data already unloads
        # explicitly, so the assert(loaded) check would fail there — gate on
        # state.
        if entry.state == config_entries.ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


class TestSetupEntry:
    async def test_all_platforms_loaded(self, hass, setup_integration):
        entry = setup_integration
        # Coordinator + client + shim all stashed on hass.data.
        bucket = hass.data[DOMAIN][entry.entry_id]
        assert "coordinator" in bucket
        assert "client" in bucket
        assert "shim" in bucket

    async def test_expected_sensor_entities_present(self, hass, setup_integration):
        # The 9 user-facing sensors that have shipped since Phase 1 + the
        # Phase 3.4/3.5 expansions. Failing this test means a sensor either
        # got renamed (breaks dashboards) or vanished (regression).
        expected_keys = {
            "battery_soc",
            "battery_soc_kwh",
            "battery_capacity_kwh",
            "battery_power",
            "grid_power",
            "solar_power",
            "load_power",
            "operating_mode",
            "reserve_soc",
            # Phase 3.4 daily kWh
            "solar_today", "grid_today", "backup_today", "nonbackup_today",
            "self_consumption_pct",
            "solar_dc_today", "solar_ac_today",
            "winter_protect",
            # Phase 3.5 Bobsilvio-parity
            "earning_yesterday",
            "grid_outage_count", "off_grid_seconds",
            "battery_charge_today", "battery_discharge_today",
        }
        registry = er.async_get(hass)
        entry_id = setup_integration.entry_id
        prefix = f"{entry_id}_"
        actual_keys = {
            e.unique_id[len(prefix):]
            for e in er.async_entries_for_config_entry(registry, entry_id)
            if e.domain == "sensor" and e.unique_id.startswith(prefix)
        }
        missing = expected_keys - actual_keys
        assert not missing, f"missing sensor keys: {missing}"

    async def test_select_switch_number_platforms(self, hass, setup_integration):
        registry = er.async_get(hass)
        entry_id = setup_integration.entry_id
        prefix = f"{entry_id}_"
        entries = er.async_entries_for_config_entry(registry, entry_id)
        by_domain: dict[str, set[str]] = {}
        for e in entries:
            if e.unique_id.startswith(prefix):
                by_domain.setdefault(e.domain, set()).add(e.unique_id[len(prefix):])

        # Manual-control surface from session 16. Select's unique_id has a
        # `_select` suffix (sensor.py reuses the bare `operating_mode` key).
        assert "operating_mode_select" in by_domain.get("select", set())
        assert "allow_grid_charge" in by_domain.get("switch", set())
        assert "daylight_saving_time" in by_domain.get("switch", set())
        assert "self_consumption_reserve" in by_domain.get("number", set())
        assert "backup_reserve" in by_domain.get("number", set())

    async def test_writable_entities_tagged_config(self, hass, setup_integration):
        # Phase 3.4 session-18 tagging — writables get CONFIG category so
        # they don't clutter the main device card.
        registry = er.async_get(hass)
        writable_uids = {
            f"{setup_integration.entry_id}_operating_mode_select",
            f"{setup_integration.entry_id}_allow_grid_charge",
            f"{setup_integration.entry_id}_daylight_saving_time",
            f"{setup_integration.entry_id}_self_consumption_reserve",
            f"{setup_integration.entry_id}_backup_reserve",
        }
        for entity in registry.entities.values():
            if entity.unique_id in writable_uids:
                assert entity.entity_category == EntityCategory.CONFIG, (
                    f"{entity.entity_id} should be CONFIG"
                )

    async def test_duplicate_sensors_tagged_diagnostic(self, hass, setup_integration):
        # The sensors that are superseded by select/number entities get
        # DIAGNOSTIC so they hide from the main card but stay queryable.
        registry = er.async_get(hass)
        diagnostic_uids = {
            f"{setup_integration.entry_id}_operating_mode",   # superseded by select
            f"{setup_integration.entry_id}_reserve_soc",      # superseded by numbers
            f"{setup_integration.entry_id}_battery_capacity_kwh",  # static spec
        }
        for entity in registry.entities.values():
            if entity.unique_id in diagnostic_uids and entity.domain == "sensor":
                assert entity.entity_category == EntityCategory.DIAGNOSTIC, (
                    f"{entity.entity_id} should be DIAGNOSTIC"
                )

    async def test_services_registered(self, hass, setup_integration):
        # Predbat shim's 7+1 services + the user-facing set_tou_schedule
        # land on the service bus.
        for service in (
            "charge_start", "charge_stop",
            "discharge_start", "discharge_stop",
            "charge_freeze", "discharge_freeze",
            "idle", "debug_freeze",
            "set_tou_schedule",
        ):
            assert hass.services.has_service(DOMAIN, service), f"missing {service}"

    async def test_unload_entry_clears_data(self, hass, setup_integration):
        entry = setup_integration
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        # Services unregister when the last entry is removed.
        assert not hass.services.has_service(DOMAIN, "charge_start")
        # Per-entry data evicted.
        assert entry.entry_id not in hass.data.get(DOMAIN, {})


class TestSensorValues:
    async def test_sensor_native_values_reflect_status(self, hass, setup_integration):
        # Pull a few representative sensors and verify their state matches
        # the fake_client's DeviceStatus snapshot from conftest. Look up
        # entity_id via the registry by unique_id — entity_id slugs are
        # derived from translated names in strings.json, which would make
        # hardcoded sensor.ep_cube_* IDs brittle to translation tweaks.
        registry = er.async_get(hass)
        entry_id = setup_integration.entry_id

        def state_for(key: str):
            uid = f"{entry_id}_{key}"
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, uid)
            assert entity_id is not None, f"no sensor registered for unique_id={uid}"
            return hass.states.get(entity_id)

        assert float(state_for("battery_soc").state) == 55.0
        assert float(state_for("solar_power").state) == 1200.0
        assert float(state_for("earning_yesterday").state) == 1.23
