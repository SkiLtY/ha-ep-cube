"""Data update coordinator for EP Cube."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DeviceStatus, EPCubeClient, EPCubeError
from .const import DEFAULT_POLL_INTERVAL_SECONDS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class EPCubeCoordinator(DataUpdateCoordinator[DeviceStatus]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: EPCubeClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=DEFAULT_POLL_INTERVAL_SECONDS),
        )
        self.client = client

    async def _async_update_data(self) -> DeviceStatus:
        try:
            return await self.client.get_status()
        except EPCubeError as err:
            raise UpdateFailed(str(err)) from err
