"""The Tasmanian Transport integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_STOP_ID, CONF_STOPS, DOMAIN, UPDATE_INTERVAL_DEFAULT
from .coordinator import TasTransitDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.DEVICE_TRACKER]


def _migrate_entry_data(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Flatten stops-list config entry data back to the flat single-stop format."""
    if CONF_STOP_ID in entry.data or CONF_STOPS not in entry.data:
        return

    stops = entry.data[CONF_STOPS]
    if not stops:
        return

    _LOGGER.info(
        "Migrating tas_transit config entry %s from stops-list to flat format",
        entry.entry_id,
    )
    data = {k: v for k, v in entry.data.items() if k != CONF_STOPS}
    data.update(stops[0])
    hass.config_entries.async_update_entry(entry, data=data)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tasmanian Transport from a config entry."""
    _migrate_entry_data(hass, entry)
    hass.data.setdefault(DOMAIN, {})

    coordinator = TasTransitDataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_interval=timedelta(seconds=UPDATE_INTERVAL_DEFAULT),
        config_entry=entry,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up update listener for configuration changes
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data[DOMAIN].pop(entry.entry_id)

        # Shutdown coordinator
        await data["coordinator"].async_shutdown()

    return unload_ok