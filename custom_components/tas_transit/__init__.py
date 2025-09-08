"""The Tasmanian Transport integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, UPDATE_INTERVAL_DEFAULT
from .coordinator import TasTransitDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.DEVICE_TRACKER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tasmanian Transport from a config entry."""
    from homeassistant.helpers.device_registry import async_get as async_get_device_registry

    hass.data.setdefault(DOMAIN, {})

    # Create coordinator device for vehicle tracking
    device_registry = async_get_device_registry(hass)
    coordinator_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_coordinator")},
        name="Tasmanian Transport Coordinator",
        manufacturer="Tasmanian Transport Services",
        model="Transit Coordinator",
    )

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
        "coordinator_device": coordinator_device,
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