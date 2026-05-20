"""EP Cube integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EPCubeClient
from .const import (
    CONF_AUTH_URL,
    CONF_BASE_URL,
    CONF_CAPACITY_KWH,
    CONF_DEV_ID,
    CONF_SESSION_COOKIE,
    CONF_SG_SN,
    DOMAIN,
)
from .coordinator import EPCubeCoordinator
from .services import (
    PredbatShim,
    async_register_services,
    async_unregister_services,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    base_url = entry.data[CONF_BASE_URL]
    client = EPCubeClient(
        session=session,
        base_url=base_url,
        auth_url=entry.data.get(CONF_AUTH_URL) or base_url,
        dev_id=entry.data[CONF_DEV_ID],
        sg_sn=entry.data[CONF_SG_SN],
        session_cookie=entry.data.get(CONF_SESSION_COOKIE) or None,
        capacity_kwh=entry.data.get(CONF_CAPACITY_KWH, 0.0),
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

    # Register services once, on first entry setup. Subsequent entries reuse them.
    if len(hass.data[DOMAIN]) == 1:
        await async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Cancel any pending revert timer before tearing down
        shim: PredbatShim | None = hass.data[DOMAIN][entry.entry_id].get("shim")
        if shim is not None:
            shim.cancel_revert_timer()
        hass.data[DOMAIN].pop(entry.entry_id)
        # Last entry gone — unregister services
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)
    return unload_ok
