"""The Dynamic tariff threshold numbers (buy-below / sell-above).

They are writable only in Dynamic mode, never while the shim has a forced export or
charge running, and never to values that look like the shim's own leftovers.
"""
from dataclasses import replace

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.ep_cube.api import EPCubeError
from custom_components.ep_cube.const import DOMAIN
from tests.test_setup import setup_integration  # noqa: F401  (fixture)


def _entity(hass, entry, key):
    registry = er.async_get(hass)
    for e in er.async_entries_for_config_entry(registry, entry.entry_id):
        if e.domain == "number" and e.unique_id == f"{entry.entry_id}_{key}":
            return e.entity_id
    raise AssertionError(key)


async def _set_dynamic(hass, entry, fake_client, buy, sell):
    """Make the cube report Dynamic mode with these prices."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    fake_client.get_status.return_value = replace(
        fake_client.get_status.return_value, dynamic_buy_below=buy, dynamic_sell_above=sell
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def _write(hass, entity_id, value):
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True
    )


@pytest.fixture
def dynamic_live(get_switch_mode):
    """What getSwitchMode returns for a cube in Dynamic mode."""
    return {**get_switch_mode, "workStatus": "2", "touType": 1,
            "dynamicChargePrice": 18.5, "dynamicDischargePrice": 30.0}


class TestAvailability:
    async def test_unavailable_outside_dynamic_mode(self, hass, setup_integration):
        for key in ("dynamic_buy_below_set", "dynamic_sell_above_set"):
            assert hass.states.get(_entity(hass, setup_integration, key)).state == "unavailable"

    async def test_shows_the_cubes_prices_in_dynamic_mode(
        self, hass, setup_integration, fake_client
    ):
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        assert float(hass.states.get(_entity(hass, setup_integration, "dynamic_buy_below_set")).state) == 18.5
        assert float(hass.states.get(_entity(hass, setup_integration, "dynamic_sell_above_set")).state) == 30.0

    async def test_unavailable_while_a_forced_export_is_running(
        self, hass, setup_integration, fake_client
    ):
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        shim = hass.data[DOMAIN][setup_integration.entry_id]["shim"]
        await shim.force_export(5, 55.0)
        coordinator = hass.data[DOMAIN][setup_integration.entry_id]["coordinator"]
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(_entity(hass, setup_integration, "dynamic_buy_below_set")).state == "unavailable"


class TestWriting:
    async def test_writes_the_price_and_keeps_the_other(
        self, hass, setup_integration, fake_client, dynamic_live
    ):
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        fake_client.get_switch_mode.return_value = dynamic_live
        await _write(hass, _entity(hass, setup_integration, "dynamic_buy_below_set"), 12.345)
        payload = fake_client.switch_mode.await_args.args[0]
        assert payload["dynamicChargePrice"] == 12.35          # rounded to the pence step
        assert payload["dynamicDischargePrice"] == 30.0        # untouched
        assert payload["touType"] == 1
        assert fake_client.switch_mode.await_args.kwargs["verify_keys"] == ("dynamicChargePrice",)

    async def test_patches_the_shims_baseline(
        self, hass, setup_integration, fake_client, dynamic_live
    ):
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        fake_client.get_switch_mode.return_value = dynamic_live
        shim = hass.data[DOMAIN][setup_integration.entry_id]["shim"]
        shim._baseline = dict(dynamic_live)
        await _write(hass, _entity(hass, setup_integration, "dynamic_sell_above_set"), 22.0)
        assert shim._baseline["dynamicDischargePrice"] == 22.0

    async def test_nothing_is_written_while_an_override_is_running(
        self, hass, setup_integration, fake_client, dynamic_live
    ):
        # The entity is unavailable then, so Home Assistant skips the service call;
        # the method itself also refuses, as a backstop for direct callers.
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        entity_id = _entity(hass, setup_integration, "dynamic_buy_below_set")
        shim = hass.data[DOMAIN][setup_integration.entry_id]["shim"]
        await shim.force_export(5, 55.0)
        writes = fake_client.switch_mode.await_count
        await _write(hass, entity_id, 10.0)
        assert fake_client.switch_mode.await_count == writes
        number = next(
            e for e in hass.data["number"].entities
            if getattr(e, "entity_description", None) is not None
            and e.entity_description.key == "dynamic_buy_below_set"
        )
        with pytest.raises(HomeAssistantError, match="forced export or charge is running"):
            await number.async_set_native_value(10.0)
        assert fake_client.switch_mode.await_count == writes

    async def test_refused_outside_dynamic_mode(self, hass, setup_integration, fake_client):
        entity = hass.data[DOMAIN][setup_integration.entry_id]
        number = next(
            e for e in hass.data["number"].entities
            if getattr(e, "entity_description", None) is not None
            and e.entity_description.key == "dynamic_buy_below_set"
        )
        with pytest.raises(HomeAssistantError, match="not in Dynamic tariff mode"):
            await number.async_set_native_value(10.0)
        assert entity  # the entry is loaded

    @pytest.mark.parametrize(
        ("field", "value", "other"),
        [
            ("dynamic_buy_below_set", 0.0, 3.0),      # buy 0 with sell 0-5: a forced export
            ("dynamic_sell_above_set", 2.0, 0.0),
        ],
    )
    async def test_refuses_values_that_look_like_a_forced_export(
        self, hass, setup_integration, fake_client, dynamic_live, field, value, other
    ):
        key_other = "dynamicDischargePrice" if field == "dynamic_buy_below_set" else "dynamicChargePrice"
        dynamic_live = {**dynamic_live, key_other: other}
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        fake_client.get_switch_mode.return_value = dynamic_live
        writes = fake_client.switch_mode.await_count
        with pytest.raises(HomeAssistantError, match="mistaken"):
            await _write(hass, _entity(hass, setup_integration, field), value)
        assert fake_client.switch_mode.await_count == writes

    async def test_a_cloud_error_is_reported(
        self, hass, setup_integration, fake_client, dynamic_live
    ):
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        fake_client.get_switch_mode.return_value = dynamic_live
        fake_client.switch_mode.side_effect = EPCubeError("cloud down")
        with pytest.raises(HomeAssistantError, match="failed to set"):
            await _write(hass, _entity(hass, setup_integration, "dynamic_buy_below_set"), 10.0)

    async def test_the_range_stays_below_the_shims_always_value(self, hass, setup_integration):
        for key in ("dynamic_buy_below_set", "dynamic_sell_above_set"):
            state = hass.states.get(_entity(hass, setup_integration, key))
            assert state.attributes["min"] == 0
            assert state.attributes["max"] == 500.0
            assert state.attributes["max"] < 999.99

class TestPredbatGate:
    @pytest.mark.parametrize("state", ["off", "unavailable"])
    async def test_refused_while_predbat_is_in_control(
        self, hass, setup_integration, fake_client, dynamic_live, state
    ):
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        fake_client.get_switch_mode.return_value = dynamic_live
        hass.states.async_set("switch.predbat_set_read_only", state)
        writes = fake_client.switch_mode.await_count
        with pytest.raises(HomeAssistantError, match="Predbat is controlling this cube"):
            await _write(hass, _entity(hass, setup_integration, "dynamic_buy_below_set"), 10.0)
        assert fake_client.switch_mode.await_count == writes

    async def test_the_value_stays_readable_while_predbat_is_in_control(
        self, hass, setup_integration, fake_client
    ):
        hass.states.async_set("switch.predbat_set_read_only", "off")
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        state = hass.states.get(_entity(hass, setup_integration, "dynamic_buy_below_set"))
        assert float(state.state) == 18.5

    async def test_allowed_in_predbat_monitor_mode(
        self, hass, setup_integration, fake_client, dynamic_live
    ):
        await _set_dynamic(hass, setup_integration, fake_client, 18.5, 30.0)
        fake_client.get_switch_mode.return_value = dynamic_live
        hass.states.async_set("switch.predbat_set_read_only", "on")
        await _write(hass, _entity(hass, setup_integration, "dynamic_buy_below_set"), 10.0)
        assert fake_client.switch_mode.await_args.args[0]["dynamicChargePrice"] == 10.0
