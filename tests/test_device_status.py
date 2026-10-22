"""Status flags, firmware, model and serial number diagnostics."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ep_cube.api import (
    DeviceDetails,
    EPCubeClient,
    EPCubeError,
    FirmwareInfo,
    _flag_or_none,
    _int_or_none,
)
from custom_components.ep_cube.binary_sensor import (
    BINARY_SENSORS,
    FIRMWARE_AUTO_UPDATE,
    EPCubeAutoUpdateBinarySensor,
    EPCubeBinarySensor,
)
from custom_components.ep_cube.const import DOMAIN
from custom_components.ep_cube.coordinator import WARRANTY_REFRESH, EPCubeCoordinator
from custom_components.ep_cube.sensor import (
    FIRMWARE_VERSION_SENSOR,
    MODEL_SENSOR,
    RTU_SERIAL_NUMBER_SENSOR,
    SERIAL_NUMBER_SENSOR,
    STATUS_SENSORS,
    EPCubeDetailsSensor,
    EPCubeFirmwareSensor,
)

from .test_setup import setup_integration  # noqa: F401,E402  (shared fixture)


@pytest.fixture
def client() -> EPCubeClient:
    return EPCubeClient(
        session=MagicMock(), base_url="https://mock.invalid",
        dev_id="5613", sg_sn="x", bearer_token="t",
    )


class TestHelpers:
    @pytest.mark.parametrize(
        "value, expected",
        [("1", True), ("0", False), (1, True), (0, False), (True, True), (False, False),
         (None, None), ("", None)],
    )
    def test_flag_or_none(self, value, expected):
        assert _flag_or_none(value) is expected

    @pytest.mark.parametrize("value, expected", [("4", 4), (4, 4), (4.0, 4), (None, None), ("", None), ("x", None)])
    def test_int_or_none(self, value, expected):
        assert _int_or_none(value) == expected


class TestFirmwareInfo:
    async def test_parses_the_versions_and_the_cloud_s_spelling_of_auto_update(self, client):
        client._get = AsyncMock(return_value={
            "rtuVersion": "V1.1.0.1", "aotuUpdateFirmware": "1", "firmwareVersion": "V1.3.1",
        })
        assert await client.get_firmware_info() == FirmwareInfo("V1.3.1", "V1.1.0.1", True)

    async def test_auto_update_off(self, client):
        client._get = AsyncMock(return_value={"firmwareVersion": "V1.3.1", "aotuUpdateFirmware": "0"})
        info = await client.get_firmware_info()
        assert info.auto_update is False and info.rtu_version is None

    @pytest.mark.parametrize("payload", [None, [], "x", {}, {"message": "Not Found", "status": 404}])
    async def test_nothing_usable_gives_none(self, client, payload):
        client._get = AsyncMock(return_value=payload)
        assert await client.get_firmware_info() is None


class TestDeviceDetails:
    async def test_reads_only_the_identity_fields(self, client):
        client._get = AsyncMock(return_value={
            "modelType": "EP Cube HES-EU2-S7-20G", "sgSn": "SN1", "rtuSn": "RTU1",
            "name": "Owner Name", "address": "1 Some Street", "zipCode": "AB1 2CD",
        })
        details = await client.get_device_details()
        assert details == DeviceDetails("EP Cube HES-EU2-S7-20G", "SN1", "RTU1")
        assert not any("Owner" in str(v) or "Street" in str(v) or "AB1" in str(v) for v in vars_of(details))

    @pytest.mark.parametrize("payload", [None, [], "x", {}, {"name": "Only a name"}])
    async def test_nothing_usable_gives_none(self, client, payload):
        client._get = AsyncMock(return_value=payload)
        assert await client.get_device_details() is None


def vars_of(details: DeviceDetails):
    return [details.model, details.serial_number, details.rtu_serial_number]


class TestStatusFieldsFromHomeDeviceInfo:
    async def test_flags_and_codes_are_read(self, client):
        info = {
            "isOnline": "1", "isFault": "0", "isAlert": "1", "faultWarningType": "1",
            "signalLevel": 4, "systemStatus": 4, "winterMode": 0,
            "lppTimeDuration": 0, "lpcTimeDuration": 0, "backupLoadsMode": 1,
            "batteryPackNum": 4, "workStatus": "1", "batterySoc": 50,
            "batteryCurrentElectricity": 10.0,
        }
        mode = {"workStatus": "1", "chargingLimitSoc": "100", "selfConsumptioinReserveSoc": "20",
                "backupPowerReserveSoc": "100", "allowChargingXiaGrid": "1", "touType": 0}
        client._get = AsyncMock(side_effect=lambda path: info if "homeDeviceInfo" in path else mode)
        status = await client.get_status()
        assert status.is_online is True and status.is_fault is False and status.is_alert is True
        assert status.fault_warning_type == "1"
        assert status.signal_level == 4 and status.system_status == 4 and status.winter_mode == 0
        assert status.charging_limit_soc == 100.0
        assert status.backup_loads_mode == 1 and status.lpp_time_duration == 0.0

    async def test_missing_keys_stay_none_not_false(self, client):
        info = {"batteryPackNum": 4, "workStatus": "1", "batterySoc": 50, "batteryCurrentElectricity": 10.0}
        mode = {"workStatus": "1", "selfConsumptioinReserveSoc": "20", "backupPowerReserveSoc": "100",
                "allowChargingXiaGrid": "1", "touType": 0}
        client._get = AsyncMock(side_effect=lambda path: info if "homeDeviceInfo" in path else mode)
        status = await client.get_status()
        assert status.is_online is None and status.is_fault is None and status.is_alert is None
        assert status.fault_warning_type is None and status.signal_level is None
        assert status.charging_limit_soc is None


def _coordinator(hass, client) -> EPCubeCoordinator:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    coord = EPCubeCoordinator(hass, entry, client)
    coord._battery_flow_store = MagicMock()
    return coord


class TestCoordinatorRefresh:
    async def test_firmware_and_details_are_cached_for_a_day(self, hass):
        client = MagicMock()
        client.get_firmware_info = AsyncMock(return_value=FirmwareInfo("V1.3.1", "V1.1.0.1", True))
        client.get_device_details = AsyncMock(return_value=DeviceDetails("M", "S", "R"))
        coord = _coordinator(hass, client)
        for _ in range(2):
            await coord._async_refresh_firmware()
            await coord._async_refresh_details()
        assert coord.firmware.firmware_version == "V1.3.1" and coord.details.model == "M"
        assert client.get_firmware_info.await_count == 1 and client.get_device_details.await_count == 1
        coord._firmware_fetched -= WARRANTY_REFRESH + timedelta(minutes=1)
        coord._details_fetched -= WARRANTY_REFRESH + timedelta(minutes=1)
        await coord._async_refresh_firmware()
        await coord._async_refresh_details()
        assert client.get_firmware_info.await_count == 2 and client.get_device_details.await_count == 2

    async def test_failures_keep_the_previous_values(self, hass):
        client = MagicMock()
        first = FirmwareInfo("V1.3.1", None, None)
        client.get_firmware_info = AsyncMock(side_effect=[first, EPCubeError("x"), None])
        client.get_device_details = AsyncMock(side_effect=[DeviceDetails("M", "S", None), EPCubeError("x"), None])
        coord = _coordinator(hass, client)
        await coord._async_refresh_firmware()
        await coord._async_refresh_details()
        for _ in range(2):
            coord._firmware_fetched -= WARRANTY_REFRESH + timedelta(minutes=1)
            coord._details_fetched -= WARRANTY_REFRESH + timedelta(minutes=1)
            await coord._async_refresh_firmware()
            await coord._async_refresh_details()
        assert coord.firmware == first and coord.details.serial_number == "S"

    async def test_the_device_page_gets_model_serial_and_firmware(self, hass, mock_config_entry):
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name="EP Cube",
            model="EP Cube",
        )
        client = MagicMock()
        coord = EPCubeCoordinator(hass, entry, client)
        coord._battery_flow_store = MagicMock()
        coord._details = DeviceDetails("EP Cube HES-EU2-S7-20G", "SN-1", "R")
        coord._firmware = FirmwareInfo("V1.3.1", "V1.1.0.1", True)
        coord._async_update_device_registry()
        updated = dr.async_get(hass).async_get(device.id)
        assert updated.model == "EP Cube HES-EU2-S7-20G"
        assert updated.serial_number == "SN-1"
        assert updated.sw_version == "V1.3.1"

    async def test_no_device_yet_is_harmless(self, hass):
        coord = _coordinator(hass, MagicMock())
        coord._details = DeviceDetails("M", "S", None)
        coord._async_update_device_registry()   # nothing registered: must not raise


def _coord_ns(status=None, firmware=None, details=None):
    return SimpleNamespace(
        data=status, firmware=firmware, details=details,
        last_update_success=True, async_add_listener=MagicMock(),
    )


class TestEntities:
    def test_binary_sensors_follow_the_status_flags(self):
        status = SimpleNamespace(is_online=True, is_fault=False, is_alert=None)
        coord = _coord_ns(status=status)
        by_key = {d.key: EPCubeBinarySensor(coord, "e", d) for d in BINARY_SENSORS}
        assert by_key["online"].is_on is True
        assert by_key["fault"].is_on is False
        assert by_key["alert"].is_on is None     # unknown, not a made-up off

    def test_binary_sensors_unknown_before_the_first_poll(self):
        coord = _coord_ns(status=None)
        assert EPCubeBinarySensor(coord, "e", BINARY_SENSORS[0]).is_on is None

    def test_auto_update_flag(self):
        coord = _coord_ns(firmware=FirmwareInfo("V1", "R1", False))
        sensor = EPCubeAutoUpdateBinarySensor(coord, "e", FIRMWARE_AUTO_UPDATE)
        assert sensor.is_on is False and sensor.available is True
        gone = EPCubeAutoUpdateBinarySensor(_coord_ns(), "e", FIRMWARE_AUTO_UPDATE)
        assert gone.is_on is None and gone.available is False

    def test_firmware_sensors(self):
        coord = _coord_ns(firmware=FirmwareInfo("V1.3.1", "V1.1.0.1", True))
        assert EPCubeFirmwareSensor(coord, "e", FIRMWARE_VERSION_SENSOR, "firmware_version").native_value == "V1.3.1"
        none = EPCubeFirmwareSensor(_coord_ns(), "e", FIRMWARE_VERSION_SENSOR, "firmware_version")
        assert none.native_value is None and none.available is False

    def test_model_and_serial_sensors(self):
        coord = _coord_ns(details=DeviceDetails("EP Cube HES-EU2-S7-20G", "SN-9", "R-9"))
        assert EPCubeDetailsSensor(coord, "e", MODEL_SENSOR, "model").native_value == "EP Cube HES-EU2-S7-20G"
        assert EPCubeDetailsSensor(coord, "e", SERIAL_NUMBER_SENSOR, "serial_number").native_value == "SN-9"
        assert EPCubeDetailsSensor(coord, "e", RTU_SERIAL_NUMBER_SENSOR, "rtu_serial_number").native_value == "R-9"
        assert EPCubeDetailsSensor(_coord_ns(), "e", MODEL_SENSOR, "model").available is False

    def test_the_unclear_fields_are_disabled_by_default_and_the_rest_enabled(self):
        disabled = {d.key for d in STATUS_SENSORS if not d.entity_registry_enabled_default}
        assert disabled == {"lpp_time_duration", "lpc_time_duration", "backup_loads_mode"}
        assert RTU_SERIAL_NUMBER_SENSOR.entity_registry_enabled_default is False
        hidden = {d.key for d in STATUS_SENSORS if not d.entity_registry_visible_default}
        assert hidden == {"fault_warning_type"}
        assert MODEL_SENSOR.entity_registry_enabled_default is True

    def test_every_new_sensor_is_a_diagnostic(self):
        for desc in (*STATUS_SENSORS, MODEL_SENSOR, SERIAL_NUMBER_SENSOR, FIRMWARE_VERSION_SENSOR, *BINARY_SENSORS):
            assert desc.entity_category is not None, desc.key


class TestSetupRegistersThem:
    async def test_all_new_entities_are_registered(self, hass, setup_integration):
        registry = er.async_get(hass)
        entry_id = setup_integration.entry_id
        keys = {
            (e.domain, e.unique_id[len(entry_id) + 1:])
            for e in er.async_entries_for_config_entry(registry, entry_id)
        }
        for key in ("online", "fault", "alert", "firmware_auto_update"):
            assert ("binary_sensor", key) in keys
        for key in ("model", "serial_number", "firmware_version", "signal_level", "charging_limit_soc"):
            assert ("sensor", key) in keys


class TestConnectionType:
    @pytest.mark.parametrize(
        ("code", "expected"), [(1, "wifi"), (3, "ethernet"), (2, "unknown"), (None, None)]
    )
    def test_value(self, code, expected):
        from custom_components.ep_cube.sensor import CONNECTION_TYPE_SENSOR

        assert CONNECTION_TYPE_SENSOR.value_fn(SimpleNamespace(networking=code)) == expected
