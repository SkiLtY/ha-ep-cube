"""EP Cube integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EPCubeClient
from .captcha import make_reauth_callback
from .const import (
    CONF_BASE_URL,
    CONF_BEARER_TOKEN,
    CONF_CAPACITY_KWH,
    CONF_DEV_ID,
    CONF_PASSWORD,
    CONF_SG_SN,
    CONF_USERNAME,
    DOMAIN,
)
from .coordinator import EPCubeCoordinator
from .services import (
    PredbatShim,
    async_register_services,
    async_unregister_services,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    base_url = entry.data[CONF_BASE_URL]
    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)

    # Reauth callback runs the full captcha login flow with the stored
    # credentials. The client invokes it once on any 403, replaces its
    # in-memory token, and retries. We persist the refreshed token back
    # to entry.data so HA restarts after a refresh don't waste a captcha
    # solve on a known-good cached token.
    reauth_callback = None
    if username and password:
        bare_reauth = make_reauth_callback(
            session, base_url=base_url, username=username, password=password
        )

        async def _reauth_and_persist() -> str | None:
            token = await bare_reauth()
            if token is not None:
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_BEARER_TOKEN: token}
                )
            return token

        reauth_callback = _reauth_and_persist

    client = EPCubeClient(
        session=session,
        base_url=base_url,
        dev_id=entry.data[CONF_DEV_ID],
        sg_sn=entry.data[CONF_SG_SN],
        bearer_token=entry.data.get(CONF_BEARER_TOKEN) or None,
        capacity_kwh=entry.data.get(CONF_CAPACITY_KWH, 0.0),
        reauth_callback=reauth_callback,
    )

    coordinator = EPCubeCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    shim = PredbatShim(hass=hass, entry_id=entry.entry_id, client=client)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "shim": shim,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if len(hass.data[DOMAIN]) == 1:
        await async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        shim: PredbatShim | None = hass.data[DOMAIN][entry.entry_id].get("shim")
        if shim is not None:
            shim.cancel_revert_timer()
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)
    return unload_ok
