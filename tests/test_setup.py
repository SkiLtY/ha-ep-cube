"""Integration setup tests: async_setup_entry wires up all platforms.

Patches EPCubeClient at the integration boundary so no HTTP fires, then
asserts the expected entity IDs land on the bus and their categories are
correctly tagged (CONFIG for writables, DIAGNOSTIC for duplicates).
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.ep_cube.const import (
    CONF_FORCE_EXPORT_MODE,
    DOMAIN,
    FORCE_EXPORT_MODE_DYNAMIC,
    FORCE_EXPORT_MODE_TOU_PEAK,
)


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
        # Coordinator + stats_coordinator (Phase 4.2) + client + shim all
        # stashed on hass.data.
        bucket = hass.data[DOMAIN][entry.entry_id]
        assert "coordinator" in bucket
        assert "stats_coordinator" in bucket
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
            # Phase 3.4 daily kWh (grid_today + nonbackup_today removed in
            # v1.1.0 — replaced by the Phase 4.2 stats sensors below).
            "solar_today", "backup_today",
            "self_consumption_pct",
            "solar_dc_today", "solar_ac_today",
            "winter_protect",
            # Phase 3.5
            "earning_yesterday",
            "grid_outage_count", "off_grid_seconds",
            "battery_charge_today", "battery_discharge_today",
            # Phase 4.2 stats sensors (queryDataElectricityV2)
            "grid_import_today", "grid_export_today", "grid_net_today",
            "grid_import_yesterday", "grid_export_yesterday", "grid_net_yesterday",
            "solar_yesterday", "backup_yesterday",
            # v1.2 derived percentages from stats coordinator buckets
            "self_consumption_today", "self_consumption_yesterday",
            "self_sufficiency_today", "self_sufficiency_yesterday",
            # v1.2 cube-native month + year rollups (scope=2/3)
            "grid_import_month", "grid_export_month",
            "solar_month", "backup_month",
            "grid_import_year", "grid_export_year",
            "solar_year", "backup_year",
            # v1.2 instant-KPI tiles (derived from homeDeviceInfo power channels)
            "instant_self_consumption_pct",
            "instant_self_sufficiency_pct",
            "instant_grid_flow_w",
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
        # Session 36: Predbat write-target stubs (number + select) — required
        # for the `charge_limit` + `inverter_mode` apps.yaml entity-first
        # contract. Pure state-trackers; no cube I/O.
        assert "predbat_charge_limit" in by_domain.get("number", set())
        assert "predbat_inverter_mode" in by_domain.get("select", set())

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
            f"{setup_integration.entry_id}_predbat_charge_limit",
            f"{setup_integration.entry_id}_predbat_inverter_mode",
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
        # Predbat shim's 7 services + the debug_freeze diagnostic land on
        # the service bus. (v1.0 dropped the user-facing set_tou_schedule
        # in favour of an HA Repair flow — see repairs.py.)
        for service in (
            "charge_start", "charge_stop",
            "discharge_start", "discharge_stop",
            "charge_freeze", "discharge_freeze",
            "idle", "debug_freeze",
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


class TestStaleEntityPurge:
    """v1.1.1 cleanup: removes registry entries for sensors deleted in v1.1.0
    (grid_today + nonbackup_today). Ghosts otherwise sit as `unavailable`
    until a user manually deletes them in HA's UI.

    aiohttp 3.13+ spawns a `_run_safe_shutdown_loop` daemon thread on
    ClientSession creation that PHCC's verify_cleanup flags as lingering.
    Same filter test_api_client.py uses — scoped here to keep the existing
    TestSetupEntry / TestSensorValues teardown semantics unchanged."""

    @pytest.fixture(autouse=True)
    def filter_aiohttp_shutdown_thread(self, monkeypatch):
        import threading

        orig = threading.enumerate

        def _filtered():
            return [t for t in orig() if "_run_safe_shutdown_loop" not in t.name]

        monkeypatch.setattr(threading, "enumerate", _filtered)

    async def test_pre_existing_grid_today_entry_purged_on_setup(
        self, hass, mock_config_entry, fake_client,
    ):
        from unittest.mock import AsyncMock, patch
        from homeassistant.helpers import entity_registry as er

        entry = mock_config_entry()
        entry.add_to_hass(hass)

        # Pre-seed the registry as if the user had upgraded from v1.0 →
        # v1.1.1: stale rows pointing at the deleted unique_ids. Signature
        # is async_get_or_create(domain, platform, unique_id, ...) — first
        # positional is the *entity* domain ("sensor"); second is the
        # *integration's* platform (DOMAIN = "ep_cube"); the config_entry
        # link is what lets async_entries_for_config_entry find them later.
        registry = er.async_get(hass)
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_grid_today",
            config_entry=entry,
        )
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_nonbackup_today",
            config_entry=entry,
        )

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

        # Both ghost entries removed. Look up by unique_id should return None.
        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_grid_today",
        ) is None
        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_nonbackup_today",
        ) is None

        # Teardown — release the coordinator timer.
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    async def test_purge_is_idempotent_when_registry_clean(
        self, hass, mock_config_entry, fake_client,
    ):
        # Fresh install: registry doesn't have the ghost entries. Setup
        # should succeed with the purge as a no-op.
        from unittest.mock import AsyncMock, patch

        entry = mock_config_entry()
        entry.add_to_hass(hass)

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

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


class TestOptionsAndUnload:
    async def test_changing_the_option_reloads_with_the_new_mode(self, hass, setup_integration):
        entry = setup_integration
        old_shim = hass.data[DOMAIN][entry.entry_id]["shim"]
        assert old_shim.force_export_mode == FORCE_EXPORT_MODE_TOU_PEAK
        hass.config_entries.async_update_entry(
            entry, options={CONF_FORCE_EXPORT_MODE: FORCE_EXPORT_MODE_DYNAMIC}
        )
        await hass.async_block_till_done()
        new_shim = hass.data[DOMAIN][entry.entry_id]["shim"]
        assert new_shim is not old_shim
        assert new_shim.force_export_mode == FORCE_EXPORT_MODE_DYNAMIC

    async def test_unload_reverts_an_active_override(self, hass, setup_integration, fake_client):
        entry = setup_integration
        shim = hass.data[DOMAIN][entry.entry_id]["shim"]
        await shim.discharge_start(
            end_time=dt_util.utcnow() + timedelta(minutes=30), target_soc_pct=50.0
        )
        assert shim.is_active
        assert fake_client.switch_mode.await_count == 1        # the override
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert fake_client.switch_mode.await_count == 2        # the revert
        assert not shim.is_active

    async def test_unload_with_nothing_active_writes_nothing(
        self, hass, setup_integration, fake_client
    ):
        entry = setup_integration
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert fake_client.switch_mode.await_count == 0


class TestDebugProbe:
    """ep_cube.debug_probe: whitelisted read-only GETs with a masked response."""

    async def test_returns_masked_data_and_adds_dev_id(
        self, hass, setup_integration, fake_client
    ):
        fake_client.probe = AsyncMock(return_value={
            "tariff": "Go", "ownerEmail": "a@b.c", "lat": 51.5, "unit": "kWh",
            "rows": list(range(30)),
            "name": "A Person - AB1 2CD", "wifiName": "home-wifi",
        })
        result = await hass.services.async_call(
            DOMAIN, "debug_probe", {"route": "device/getSettingsData"},
            blocking=True, return_response=True,
        )
        fake_client.probe.assert_awaited_once_with(
            "device/getSettingsData", {"devId": "5613"}
        )
        assert result["ok"] is True
        data = result["data"]
        assert data["tariff"] == "Go" and data["unit"] == "kWh"
        assert data["ownerEmail"] == "<redacted>" and data["lat"] == "<redacted>"
        assert data["name"] == "<redacted>" and data["wifiName"] == "<redacted>"
        assert len(data["rows"]) == 21 and data["rows"][-1] == "... 10 more"

    async def test_full_keeps_whole_lists(self, hass, setup_integration, fake_client):
        fake_client.probe = AsyncMock(return_value={"rows": list(range(30))})
        result = await hass.services.async_call(
            DOMAIN, "debug_probe",
            {"route": "device/queryDataGraphV2", "full": True,
             "params": {"scopeType": "1"}},
            blocking=True, return_response=True,
        )
        assert len(result["data"]["rows"]) == 30
        fake_client.probe.assert_awaited_once_with(
            "device/queryDataGraphV2", {"devId": "5613", "scopeType": "1"}
        )

    async def test_rejects_routes_off_the_whitelist(
        self, hass, setup_integration, fake_client
    ):
        import voluptuous as vol
        fake_client.probe = AsyncMock()
        for route in ("device/switchMode", "device/getAddressById", "../x"):
            with pytest.raises(vol.Invalid):
                await hass.services.async_call(
                    DOMAIN, "debug_probe", {"route": route},
                    blocking=True, return_response=True,
                )
        fake_client.probe.assert_not_awaited()

    async def test_cloud_error_is_reported_not_raised(
        self, hass, setup_integration, fake_client
    ):
        from custom_components.ep_cube.api import EPCubeError
        fake_client.probe = AsyncMock(side_effect=EPCubeError("404 not found"))
        result = await hass.services.async_call(
            DOMAIN, "debug_probe", {"route": "device/getSettingsData"},
            blocking=True, return_response=True,
        )
        assert result["ok"] is False and "404" in result["error"]


class TestPVStringSensors:
    async def test_pv_sensors_registered_and_valued(self, hass, setup_integration):
        registry = er.async_get(hass)
        entry_id = setup_integration.entry_id
        by_uid = {
            e.unique_id[len(entry_id) + 1:]: e
            for e in er.async_entries_for_config_entry(registry, entry_id)
            if e.domain == "sensor"
        }
        for n in range(1, 5):
            for kind in ("power", "voltage", "current"):
                assert f"pv{n}_{kind}" in by_uid
        # Strings 3 and 4 exist but are disabled by default.
        assert by_uid["pv1_power"].disabled_by is None
        assert by_uid["pv3_power"].disabled_by is not None
        state = hass.states.get(by_uid["pv2_voltage"].entity_id)
        assert float(state.state) == 179.4
        assert float(hass.states.get(by_uid["pv1_power"].entity_id).state) == 180.0

    async def test_failed_pv_fetch_keeps_previous_values(self, hass, setup_integration, fake_client):
        from custom_components.ep_cube.api import EPCubeError
        coordinator = hass.data[DOMAIN][setup_integration.entry_id]["coordinator"]
        assert coordinator.pv_strings[1]["power_w"] == 180.0
        fake_client.get_pv_strings.side_effect = EPCubeError("cloud down")
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.pv_strings[1]["power_w"] == 180.0


class TestDebugProbeWrite:
    LIVE = {
        "workStatus": "2", "touType": 0, "selfConsumptioinReserveSoc": "20",
        "backupPowerReserveSoc": "100", "dynamicChargePrice": 0.0,
        "dynamicDischargePrice": 999.99, "allowChargingXiaGrid": "1",
        "offPeakTimeList": ["00:30_05:30_2.22"],
    }
    REL = {
        "brandId": "99", "supportCountryId": "237", "productBrand": "OCTOPUS_ENERGY",
        "productCode": "AGILE-24-10-01", "productCodeOut": "AGILE-OUTGOING-19-05-13",
        "regionCode": "L",
    }

    async def test_portal_style_body_is_built_from_current_state(
        self, hass, setup_integration, fake_client
    ):
        fake_client.get_switch_mode = AsyncMock(return_value=dict(self.LIVE))
        fake_client.probe = AsyncMock(return_value=dict(self.REL))
        fake_client.probe_post = AsyncMock(return_value=None)
        result = await hass.services.async_call(
            DOMAIN, "debug_probe_write", {"route": "device/setDynamicPrice"},
            blocking=True, return_response=True,
        )
        route, body = fake_client.probe_post.await_args.args
        assert route == "device/setDynamicPrice"
        assert body["dynamicDischargePrice"] == 999.99
        assert body["backupPowerReserveSoc"] == "100"
        assert body["dynamicPriceBinding"]["productCodeOut"] == "AGILE-OUTGOING-19-05-13"
        assert body["dynamicPriceBinding"]["regionCode"] == "L"
        assert result["ok"] is True and result["after"]["workStatus"] == "2"

    async def test_switchmode_route_carries_the_binding_on_the_full_payload(
        self, hass, setup_integration, fake_client
    ):
        fake_client.get_switch_mode = AsyncMock(return_value=dict(self.LIVE))
        fake_client.probe = AsyncMock(return_value=dict(self.REL))
        fake_client.probe_post = AsyncMock(return_value=None)
        await hass.services.async_call(
            DOMAIN, "debug_probe_write", {"route": "device/switchMode"},
            blocking=True, return_response=True,
        )
        _route, body = fake_client.probe_post.await_args.args
        assert body["workStatus"] == "2" and body["offPeakTimeList"] == ["00:30_05:30_2.22"]
        assert body["dynamicPriceBinding"]["productCode"] == "AGILE-24-10-01"

    async def test_unknown_route_and_user_body_are_rejected(
        self, hass, setup_integration, fake_client
    ):
        import voluptuous as vol
        fake_client.probe_post = AsyncMock()
        for data in ({"route": "device/deleteDevice"}, {"route": "device/switchMode", "body": {"a": 1}}):
            with pytest.raises(vol.Invalid):
                await hass.services.async_call(
                    DOMAIN, "debug_probe_write", data, blocking=True, return_response=True
                )
        fake_client.probe_post.assert_not_awaited()

    async def test_cloud_error_is_reported(self, hass, setup_integration, fake_client):
        from custom_components.ep_cube.api import EPCubeError
        fake_client.get_switch_mode = AsyncMock(return_value=dict(self.LIVE))
        fake_client.probe = AsyncMock(return_value=dict(self.REL))
        fake_client.probe_post = AsyncMock(side_effect=EPCubeError("404 not found"))
        result = await hass.services.async_call(
            DOMAIN, "debug_probe_write", {"route": "dynamicPrice/save"},
            blocking=True, return_response=True,
        )
        assert result["ok"] is False and "404" in result["error"]


    async def test_export_product_override_changes_only_the_export_code(
        self, hass, setup_integration, fake_client
    ):
        fake_client.get_switch_mode = AsyncMock(return_value=dict(self.LIVE))
        fake_client.probe = AsyncMock(return_value=dict(self.REL))
        fake_client.probe_post = AsyncMock(return_value=None)
        await hass.services.async_call(
            DOMAIN, "debug_probe_write",
            {"route": "device/switchMode", "export_product": "OUTGOING-VAR-24-10-26"},
            blocking=True, return_response=True,
        )
        _route, body = fake_client.probe_post.await_args.args
        binding = body["dynamicPriceBinding"]
        assert binding["productCodeOut"] == "OUTGOING-VAR-24-10-26"
        assert binding["productCode"] == "AGILE-24-10-01"

    async def test_other_export_products_are_rejected(
        self, hass, setup_integration, fake_client
    ):
        import voluptuous as vol
        fake_client.probe_post = AsyncMock()
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(
                DOMAIN, "debug_probe_write",
                {"route": "device/switchMode", "export_product": "SOMETHING-ELSE"},
                blocking=True, return_response=True,
            )
        fake_client.probe_post.assert_not_awaited()


    async def test_savebinding_route_sends_the_binding_block_alone(
        self, hass, setup_integration, fake_client
    ):
        fake_client.get_switch_mode = AsyncMock(return_value=dict(self.LIVE))
        fake_client.probe = AsyncMock(return_value=dict(self.REL))
        fake_client.probe_post = AsyncMock(return_value=None)
        await hass.services.async_call(
            DOMAIN, "debug_probe_write",
            {"route": "dynamicPrice/saveBinding", "export_product": "OUTGOING-VAR-24-10-26"},
            blocking=True, return_response=True,
        )
        route, body = fake_client.probe_post.await_args.args
        assert route == "dynamicPrice/saveBinding"
        assert body["devId"] == "5613" and body["regionCode"] == "L"
        assert body["productCodeOut"] == "OUTGOING-VAR-24-10-26"
        assert "workStatus" not in body
