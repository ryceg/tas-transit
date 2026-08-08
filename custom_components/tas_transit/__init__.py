"""The Tasmanian Transport integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_DESTINATION_FILTERS,
    CONF_FILTER_MODE,
    CONF_LINE_FILTERS,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    UPDATE_INTERVAL_DEFAULT,
)
from .coordinator import TasTransitDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


def _migrate_entry_data(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate old flat config entry data to the new stops list format."""
    if CONF_STOPS in entry.data:
        return

    _LOGGER.info("Migrating tas_transit config entry %s to stops format", entry.entry_id)
    stop_config: dict = {
        CONF_STOP_ID: entry.data[CONF_STOP_ID],
        CONF_STOP_NAME: entry.data.get(CONF_STOP_NAME, f"Stop {entry.data[CONF_STOP_ID]}"),
    }
    for key in (CONF_LINE_FILTERS, CONF_DESTINATION_FILTERS, CONF_FILTER_MODE):
        if key in entry.data:
            stop_config[key] = entry.data[key]

    hass.config_entries.async_update_entry(
        entry,
        data={CONF_STOPS: [stop_config]},
    )


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
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data[DOMAIN].pop(entry.entry_id)
        
        # Shutdown coordinator
        await data["coordinator"].async_shutdown()
    
    return unload_ok