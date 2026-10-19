"""The battery charged/discharged-today totals survive a Home Assistant restart."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE

from custom_components.ep_cube import api as api_mod
from custom_components.ep_cube.api import EPCubeClient
from custom_components.ep_cube.coordinator import battery_flow_store


@pytest.fixture
def client() -> EPCubeClient:
    return EPCubeClient(
        session=MagicMock(),
        base_url="https://mock.invalid",
        dev_id="5613",
        sg_sn="x",
        bearer_token="t",
    )


def _saved(**overrides):
    state = {
        "date": api_mod._local_today().isoformat(),
        "charged_kwh": 4.0,
        "discharged_kwh": 2.5,
        "last_kwh": 11.0,
    }
    state.update(overrides)
    return state


class TestStateRoundTrip:
    def test_round_trip_into_a_fresh_client(self, client):
        client._update_battery_flow(11.0)
        client._update_battery_flow(11.5)
        client._update_battery_flow(11.2)
        state = client.battery_flow_state()

        fresh = EPCubeClient(
            session=MagicMock(), base_url="https://mock.invalid",
            dev_id="5613", sg_sn="x", bearer_token="t",
        )
        assert fresh.restore_battery_flow(state) is True
        assert fresh._battery_charge_today_kwh == pytest.approx(0.5)
        assert fresh._battery_discharge_today_kwh == pytest.approx(0.3)
        assert fresh._battery_flow_last_kwh == 11.2

    def test_yesterdays_state_is_ignored(self, client):
        assert client.restore_battery_flow(_saved(date="2020-01-01")) is False
        assert client._battery_charge_today_kwh == 0.0
        assert client._battery_flow_last_kwh is None

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "nonsense",
            [],
            {},
            _saved(charged_kwh="x"),
            _saved(charged_kwh=-1),
            _saved(discharged_kwh=float("nan")),
            _saved(last_kwh=0),
            _saved(last_kwh=float("inf")),
            _saved(date="not a date"),
        ],
    )
    def test_bad_data_is_ignored(self, client, bad):
        assert client.restore_battery_flow(bad) is False
        assert client._battery_charge_today_kwh == 0.0
        assert client._battery_discharge_today_kwh == 0.0
        assert client._battery_flow_last_kwh is None

    def test_not_restored_once_the_tracker_has_a_reading(self, client):
        client._update_battery_flow(11.0)
        assert client.restore_battery_flow(_saved()) is False
        assert client._battery_charge_today_kwh == 0.0

    def test_a_saved_state_without_an_anchor_is_accepted(self, client):
        assert client.restore_battery_flow(_saved(last_kwh=None)) is True
        client._update_battery_flow(12.0)  # first reading just anchors
        assert client._battery_charge_today_kwh == 4.0

    def test_change_during_downtime_is_counted(self, client):
        assert client.restore_battery_flow(_saved()) is True
        client._update_battery_flow(12.0)  # +1.0 since the saved 11.0 reading
        assert client._battery_charge_today_kwh == pytest.approx(5.0)
        assert client._battery_discharge_today_kwh == pytest.approx(2.5)


class TestCoordinatorPersistence:
    async def test_setup_restores_from_the_store(
        self, hass, hass_storage, mock_config_entry, fake_client
    ):
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        saved = _saved()
        hass_storage[battery_flow_store(hass, entry.entry_id).key] = {
            "version": 1,
            "key": battery_flow_store(hass, entry.entry_id).key,
            "data": saved,
        }
        with (
            patch("custom_components.ep_cube.EPCubeClient", return_value=fake_client),
            patch(
                "custom_components.ep_cube.make_reauth_callback",
                return_value=AsyncMock(return_value=None),
            ),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            fake_client.restore_battery_flow.assert_called_once_with(saved)
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()

    async def test_a_poll_writes_the_store(
        self, hass, hass_storage, mock_config_entry, fake_client
    ):
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        key = battery_flow_store(hass, entry.entry_id).key
        with (
            patch("custom_components.ep_cube.EPCubeClient", return_value=fake_client),
            patch(
                "custom_components.ep_cube.make_reauth_callback",
                return_value=AsyncMock(return_value=None),
            ),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            assert entry.state == config_entries.ConfigEntryState.LOADED
            # The save is delayed; the final-write event flushes it (as HA does on stop).
            hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
            await hass.async_block_till_done()
            assert hass_storage[key]["data"] == fake_client.battery_flow_state()
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
