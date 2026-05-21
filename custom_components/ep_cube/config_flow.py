"""Config flow for EP Cube.

User pastes a Bearer token (obtained from the mobile-app auth flow or, until
Phase 3.2 step 4 lands, from epcube-token.streamlit.app) and the integration
fetches the device list to resolve devId / sgSn / capacity.

The captcha-based in-integration login is the eventual replacement for this
paste flow — see docs/PHASE_3_2.md step 4.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    AuthError,
    EPCubeClient,
    EPCubeError,
    _capacity_string_to_kwh,
)
from .const import (
    CONF_BASE_URL,
    CONF_BEARER_TOKEN,
    CONF_CAPACITY_KWH,
    CONF_DEV_ID,
    CONF_SG_SN,
    DEFAULT_BASE_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Required(CONF_BEARER_TOKEN): str,
        vol.Optional(CONF_DEV_ID, default=""): str,
        vol.Optional(CONF_SG_SN, default=""): str,
    }
)


class EPCubeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 3

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL]
            bearer_token = (user_input.get(CONF_BEARER_TOKEN) or "").strip()
            # Strip a "Bearer " prefix if the user pasted one — the api layer
            # adds it itself.
            if bearer_token.lower().startswith("bearer "):
                bearer_token = bearer_token.split(None, 1)[1]
            dev_id_hint = user_input.get(CONF_DEV_ID) or ""
            sg_sn_hint = user_input.get(CONF_SG_SN) or ""

            if not bearer_token:
                errors["base"] = "no_auth_supplied"
            else:
                session = async_get_clientsession(self.hass)
                client = EPCubeClient(
                    session=session,
                    base_url=base_url,
                    dev_id=dev_id_hint or "0",
                    sg_sn=sg_sn_hint or "0",
                    bearer_token=bearer_token,
                )
                try:
                    devices = await client.get_device_list()
                except AuthError as err:
                    _LOGGER.warning("config_flow auth failed: %s", err)
                    errors["base"] = "invalid_auth"
                except aiohttp.ClientError as err:
                    _LOGGER.warning("config_flow network error: %s", err)
                    errors["base"] = "cannot_connect"
                except EPCubeError as err:
                    _LOGGER.warning("config_flow unknown error: %s", err)
                    errors["base"] = "unknown"
                else:
                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        chosen = _pick_device(devices, dev_id_hint, sg_sn_hint)
                        if chosen is None:
                            errors["base"] = "multiple_devices"
                        else:
                            dev_id = str(chosen.get("devId") or chosen.get("id"))
                            sg_sn = str(chosen.get("sgSn"))
                            capacity = _capacity_string_to_kwh(chosen.get("systemCapacity"))

                            await self.async_set_unique_id(dev_id)
                            self._abort_if_unique_id_configured()

                            entry_data = {
                                CONF_BASE_URL: base_url,
                                CONF_BEARER_TOKEN: bearer_token,
                                CONF_DEV_ID: dev_id,
                                CONF_SG_SN: sg_sn,
                                CONF_CAPACITY_KWH: capacity,
                            }
                            return self.async_create_entry(
                                title=f"EP Cube ({dev_id})",
                                data=entry_data,
                            )

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
        )


def _pick_device(
    devices: list[dict[str, Any]],
    dev_id_hint: str,
    sg_sn_hint: str,
) -> dict[str, Any] | None:
    """Resolve which device entry to use. Returns None if ambiguous."""
    if dev_id_hint:
        for d in devices:
            if str(d.get("devId") or d.get("id")) == dev_id_hint:
                return d
        return None
    if sg_sn_hint:
        for d in devices:
            if str(d.get("sgSn")) == sg_sn_hint:
                return d
        return None
    if len(devices) == 1:
        return devices[0]
    return None
