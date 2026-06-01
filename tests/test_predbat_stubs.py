"""Predbat write-target stub entities (session 36).

`number.ep_cube_predbat_charge_limit` and `select.ep_cube_predbat_inverter_mode`
satisfy Predbat's entity-first contract for the `charge_limit` + `inverter_mode`
apps.yaml keys. They are pure state-trackers — no cube I/O fires on receipt;
the actual cube actions still come from the 6 `*_service` calls Predbat
fires alongside.

Tests cover:
- entity defaults match Predbat's create_entity fallbacks (100% / "Eco")
- write-and-read round-trip via the HA service bus (mirrors what Predbat
  does via write_and_poll_value / write_and_poll_option)
- select option validation rejects unknown strings
- RestoreEntity wiring is present (defaults remain on no restored state)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.ep_cube.const import DOMAIN


@pytest.fixture
async def setup_integration(hass, mock_config_entry, fake_client):
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
        yield entry
        if entry.state == config_entries.ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


def _entity_id(hass, domain: str, unique_suffix: str, entry_id: str) -> str:
    registry = er.async_get(hass)
    uid = f"{entry_id}_{unique_suffix}"
    entity_id = registry.async_get_entity_id(domain, DOMAIN, uid)
    assert entity_id is not None, f"no {domain} registered for unique_id={uid}"
    return entity_id


class TestPredbatChargeLimit:
    async def test_default_value_matches_predbat_fallback(self, hass, setup_integration):
        # Predbat's own create_entity("charge_limit", 100) sets default 100.
        # Anything else and Predbat's first plan tick will retry-write 100
        # until it matches — wastes cloud budget on the cube side. Keep
        # this default in sync with inverter.py:517 in the predbat container.
        entity_id = _entity_id(
            hass, "number", "predbat_charge_limit", setup_integration.entry_id
        )
        assert float(hass.states.get(entity_id).state) == 100.0

    async def test_set_value_round_trip(self, hass, setup_integration):
        # Mirrors Predbat's write_and_poll_value path: call set_value,
        # confirm the entity reads back the written value.
        entity_id = _entity_id(
            hass, "number", "predbat_charge_limit", setup_integration.entry_id
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": 73},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert float(hass.states.get(entity_id).state) == 73.0

    async def test_set_value_accepts_zero_and_hundred(self, hass, setup_integration):
        # Range bounds — Predbat may write either extreme depending on plan.
        entity_id = _entity_id(
            hass, "number", "predbat_charge_limit", setup_integration.entry_id
        )
        for value in (0, 100):
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": value},
                blocking=True,
            )
            await hass.async_block_till_done()
            assert float(hass.states.get(entity_id).state) == float(value)


class TestPredbatInverterMode:
    async def test_default_option_matches_predbat_fallback(self, hass, setup_integration):
        # Predbat's create_entity("inverter_mode", "Eco") sets default "Eco".
        # Keep in sync with inverter.py:528-529 in the predbat container.
        entity_id = _entity_id(
            hass, "select", "predbat_inverter_mode", setup_integration.entry_id
        )
        assert hass.states.get(entity_id).state == "Eco"

    async def test_options_match_generic_inverter_branch(self, hass, setup_integration):
        # Predbat's adjust_inverter_mode (inverter.py:2059-2125) only ever
        # writes "Eco" or "Timed Export" for the generic-inverter branch
        # (not Fox, not GE eco toggle — both False in our apps.yaml). Reading
        # "Eco (Paused)" is tolerated but never written, so we don't expose it.
        entity_id = _entity_id(
            hass, "select", "predbat_inverter_mode", setup_integration.entry_id
        )
        state = hass.states.get(entity_id)
        assert set(state.attributes["options"]) == {"Eco", "Timed Export"}

    async def test_select_option_round_trip(self, hass, setup_integration):
        entity_id = _entity_id(
            hass, "select", "predbat_inverter_mode", setup_integration.entry_id
        )
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": "Timed Export"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "Timed Export"

        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": "Eco"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "Eco"

    async def test_select_option_rejects_unknown(self, hass, setup_integration):
        # Defensive backstop — Predbat shouldn't ever write a stray string,
        # but if a user fires `select.select_option` from a dashboard with
        # a typo, we surface a clear HomeAssistantError rather than silently
        # accepting a state Predbat doesn't understand.
        entity_id = _entity_id(
            hass, "select", "predbat_inverter_mode", setup_integration.entry_id
        )
        # HA's select platform rejects unknown options before reaching our
        # async_select_option — either layer raising is acceptable; what
        # matters is the entity state doesn't change.
        with pytest.raises((HomeAssistantError, ValueError)):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": "Bogus"},
                blocking=True,
            )
        assert hass.states.get(entity_id).state == "Eco"
