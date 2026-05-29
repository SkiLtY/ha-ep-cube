"""Data update coordinator for EP Cube."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

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
        # Cached raw getSwitchMode response — used by the operating-mode select
        # to expose the cube's current TOU schedule as entity attributes (so
        # the editor card can hydrate from real state). Best-effort: a failed
        # schedule fetch doesn't fail the whole poll (homeDeviceInfo is the
        # critical path).
        self._switch_mode: dict[str, Any] | None = None

    @property
    def switch_mode(self) -> dict[str, Any] | None:
        """Latest getSwitchMode snapshot, or None if it's never succeeded."""
        return self._switch_mode

    async def _async_update_data(self) -> DeviceStatus:
        try:
            status = await self.client.get_status()
        except EPCubeError as err:
            raise UpdateFailed(str(err)) from err

        # Schedule fetch is non-critical — log + keep previous cache on failure.
        try:
            self._switch_mode = await self.client.get_switch_mode()
        except EPCubeError as err:
            _LOGGER.debug("get_switch_mode failed (keeping cached): %s", err)

        return status
