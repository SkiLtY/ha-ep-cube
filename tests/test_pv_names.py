"""Friendly names for the PV strings, set in Configure (issue #17)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.helpers import entity_registry as er

from custom_components.ep_cube.const import DOMAIN, PV_NAME_KEYS, PV_NAME_MAX_LENGTH

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "ep_cube"


@pytest.fixture
async def setup_entry(hass, mock_config_entry, fake_client):
    """Set up the integration, optionally with options; always unloads at the end."""
    started = []

    async def _setup(options: dict | None = None):
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        if options:
            hass.config_entries.async_update_entry(entry, options=options)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        started.append(entry)
        return entry

    with (
        patch("custom_components.ep_cube.EPCubeClient", return_value=fake_client),
        patch(
            "custom_components.ep_cube.make_reauth_callback",
            return_value=AsyncMock(return_value=None),
        ),
    ):
        yield _setup
        for entry in started:
            if entry.state == config_entries.ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)
                await hass.async_block_till_done()


def _pv_entity(hass, entry, key):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id is not None, key
    return entity_id


class TestOptionsForm:
    async def test_the_form_has_four_name_fields_defaulting_to_blank(self, hass, mock_config_entry):
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        defaults = {str(key): key.default() for key in result["data_schema"].schema}
        for key in PV_NAME_KEYS:
            assert defaults[key] == ""

    async def test_saved_names_are_the_next_defaults(self, hass, mock_config_entry):
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(entry, options={"pv1_name": "East roof"})
        result = await hass.config_entries.options.async_init(entry.entry_id)
        defaults = {str(key): key.default() for key in result["data_schema"].schema}
        assert defaults["pv1_name"] == "East roof" and defaults["pv2_name"] == ""

    async def test_names_are_trimmed_and_limited_and_blank_stays_blank(self, hass, mock_config_entry):
        entry = mock_config_entry()
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "force_export_mode": "tou_peak",
                "force_charge_mode": "tou_offpeak",
                "pv1_name": "  East roof  ",
                "pv2_name": "W" * 80,
                "pv3_name": "   ",
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert entry.options["pv1_name"] == "East roof"
        assert entry.options["pv2_name"] == "W" * PV_NAME_MAX_LENGTH
        assert entry.options["pv3_name"] == ""
        assert entry.options["pv4_name"] == ""


class TestSensorNames:
    async def test_without_names_the_sensors_keep_their_default_names(self, hass, setup_entry):
        entry = await setup_entry()
        state = hass.states.get(_pv_entity(hass, entry, "pv1_power"))
        assert state.attributes["friendly_name"] == "EP Cube PV1 power"
        assert hass.states.get(_pv_entity(hass, entry, "pv2_voltage")).attributes["friendly_name"] == "EP Cube PV2 voltage"

    async def test_a_name_replaces_pv1_for_all_three_sensors_of_that_string(self, hass, setup_entry):
        entry = await setup_entry({"pv1_name": "East roof", "pv2_name": ""})
        for kind in ("power", "voltage", "current"):
            name = hass.states.get(_pv_entity(hass, entry, f"pv1_{kind}")).attributes["friendly_name"]
            assert name == f"EP Cube East roof {kind}"
        # An unnamed string keeps its default.
        assert hass.states.get(_pv_entity(hass, entry, "pv2_power")).attributes["friendly_name"] == "EP Cube PV2 power"

    async def test_naming_a_string_later_keeps_the_entity_id_and_unique_id(self, hass, setup_entry):
        entry = await setup_entry()
        before = _pv_entity(hass, entry, "pv1_power")
        assert before.endswith("pv1_power")
        hass.config_entries.async_update_entry(entry, options={"pv1_name": "East roof"})
        await hass.async_block_till_done()
        after = _pv_entity(hass, entry, "pv1_power")
        assert after == before            # same entity, found by the same unique ID
        assert hass.states.get(after).attributes["friendly_name"] == "EP Cube East roof power"
        assert float(hass.states.get(after).state) == 180.0   # still reads the same data


class TestTranslations:
    @pytest.mark.parametrize("path", ["strings.json", *(f"translations/{lang}.json" for lang in ("en", "de", "it", "nl"))])
    def test_every_pv_name_uses_the_placeholder(self, path):
        data = json.loads((PKG / path).read_text(encoding="utf-8"))
        names = {k: v["name"] for k, v in data["entity"]["sensor"].items() if k[:2] == "pv" and k[2].isdigit()}
        assert len(names) == 12
        assert all("{pv_name}" in n and "PV" not in n for n in names.values()), names
        init = data["options"]["step"]["init"]["data"]
        assert all(key in init for key in PV_NAME_KEYS)
