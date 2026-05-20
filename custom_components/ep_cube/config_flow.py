"""Config flow for EP Cube.

Two-path auth:
- **Real cloud**: user pastes a `JSESSIONID` cookie value from a browser DevTools
  session (the slider captcha can't be solved headlessly). Session expiry
  surfaces as a `SessionExpiredError` later — user re-runs config-flow with a
  fresh cookie.
- **Mock cloud**: leave the cookie blank and supply username/password — the
  mock's `/cas/login` accepts any creds and issues a JSESSIONID.

After auth, the device list is fetched. If exactly one device is returned, it
is auto-selected. If multiple, the user picks from a dropdown. If none, the
flow fails with a clear error.
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
    CONF_AUTH_URL,
    CONF_BASE_URL,
    CONF_CAPACITY_KWH,
    CONF_DEV_ID,
    CONF_PASSWORD,
    CONF_SESSION_COOKIE,
    CONF_SG_SN,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Optional(CONF_AUTH_URL, default=""): str,
        vol.Optional(CONF_SESSION_COOKIE, default=""): str,
        vol.Optional(CONF_USERNAME, default=""): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
        vol.Optional(CONF_DEV_ID, default=""): str,
        vol.Optional(CONF_SG_SN, default=""): str,
    }
)


class EPCubeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input[CONF_BASE_URL]
            auth_url = user_input.get(CONF_AUTH_URL) or base_url
            session_cookie = user_input.get(CONF_SESSION_COOKIE) or None
            username = user_input.get(CONF_USERNAME) or None
            password = user_input.get(CONF_PASSWORD) or None
            dev_id_hint = user_input.get(CONF_DEV_ID) or ""
            sg_sn_hint = user_input.get(CONF_SG_SN) or ""

            if not session_cookie and not (username and password):
                errors["base"] = "no_auth_supplied"
            else:
                session = async_get_clientsession(self.hass)
                client = EPCubeClient(
                    session=session,
                    base_url=base_url,
                    auth_url=auth_url,
                    dev_id=dev_id_hint or "0",  # placeholder; resolved from deviceList
                    sg_sn=sg_sn_hint or "0",
                    session_cookie=session_cookie,
                    username=username,
                    password=password,
                )
                try:
                    # Auth first (sets cookie if dev-login path), then enumerate.
                    if session_cookie is None:
                        # Dev login path — authenticate() handles it.
                        await client.authenticate()
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
                        # Pick the device. Prefer user-supplied dev_id; else
                        # auto-pick if exactly one device on the account.
                        chosen = _pick_device(devices, dev_id_hint, sg_sn_hint)
                        if chosen is None:
                            errors["base"] = "multiple_devices"
                        else:
                            dev_id = str(chosen.get("id"))
                            sg_sn = str(chosen.get("sgSn"))
                            capacity = _capacity_string_to_kwh(chosen.get("systemCapacity"))

                            await self.async_set_unique_id(dev_id)
                            self._abort_if_unique_id_configured()

                            # Persist the resolved IDs + cookie acquired via dev-login.
                            entry_data = {
                                CONF_BASE_URL: base_url,
                                CONF_AUTH_URL: auth_url,
                                CONF_SESSION_COOKIE: client._session_cookie or "",
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
            if str(d.get("id")) == dev_id_hint:
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
