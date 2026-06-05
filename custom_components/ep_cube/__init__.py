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
    CONF_API_PREFIX,
    CONF_BASE_URL,
    CONF_BEARER_TOKEN,
    CONF_CAPACITY_KWH,
    CONF_DEV_ID,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_SG_SN,
    CONF_USERNAME,
    DEFAULT_API_PREFIX,
    DOMAIN,
    REGION_OTHER,
    REGIONS,
)
from .coordinator import EPCubeCoordinator, EPCubeStatsCoordinator
from .services import (
    PredbatShim,
    async_register_services,
    async_unregister_services,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.NUMBER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    base_url = entry.data[CONF_BASE_URL]
    api_prefix = entry.data.get(CONF_API_PREFIX, DEFAULT_API_PREFIX)
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
            session,
            base_url=base_url,
            username=username,
            password=password,
            api_prefix=api_prefix,
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
        api_prefix=api_prefix,
        dev_id=entry.data[CONF_DEV_ID],
        sg_sn=entry.data[CONF_SG_SN],
        bearer_token=entry.data.get(CONF_BEARER_TOKEN) or None,
        capacity_kwh=entry.data.get(CONF_CAPACITY_KWH, 0.0),
        reauth_callback=reauth_callback,
    )

    coordinator = EPCubeCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    # Stats coordinator (Phase 4.2). Polls queryDataElectricityV2 on a
    # 5-min cadence for today's totals plus slower cadences for monthly /
    # annual / lifetime rollups. Failure on first refresh raises so HA
    # flags the integration — primary `coordinator` already succeeded, so
    # this is a clean signal that the new endpoint is unhappy.
    stats_coordinator = EPCubeStatsCoordinator(hass, entry, client)
    await stats_coordinator.async_config_entry_first_refresh()

    shim = PredbatShim(hass=hass, entry_id=entry.entry_id, client=client)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "stats_coordinator": stats_coordinator,
        "client": client,
        "shim": shim,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if len(hass.data[DOMAIN]) == 1:
        await async_register_services(hass)

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring older config entries forward.

    v4 (session 12 — captcha-login bring-up): stored CONF_BASE_URL only,
        implicit "/api" prefix, no region key.
    v5 (session 18 — Phase 3.4 (a)): adds CONF_REGION + CONF_API_PREFIX so
        US users with the "/app-api" prefix can configure cleanly.

    Migration infers region by matching the stored base_url against the
    REGIONS map. Unrecognised hosts (custom mocks, dev forks) fall back to
    REGION_OTHER + "/api" — preserves existing behaviour, no breakage.
    """
    if entry.version >= 5:
        return True

    base_url = entry.data.get(CONF_BASE_URL, "").rstrip("/")
    region = REGION_OTHER
    api_prefix = DEFAULT_API_PREFIX
    for key, info in REGIONS.items():
        if base_url == info["base_url"]:
            region = key
            api_prefix = info["api_prefix"]
            break

    new_data = {
        **entry.data,
        CONF_REGION: region,
        CONF_API_PREFIX: api_prefix,
    }
    hass.config_entries.async_update_entry(entry, data=new_data, version=5)
    _LOGGER.info(
        "migrated entry %s to v5: region=%s api_prefix=%s",
        entry.entry_id, region, api_prefix,
    )
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
