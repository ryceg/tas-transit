"""Select platform for Tasmanian Transport integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_STOP_ID,
    CONF_STOP_NAME,
    DOMAIN,
    FILTER_MODE_EXCLUDE,
    FILTER_MODE_INCLUDE,
)
from .coordinator import TasTransitDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# User-friendly filter mode options
FILTER_MODE_OPTIONS = {
    FILTER_MODE_INCLUDE: "Include (show only selected)",
    FILTER_MODE_EXCLUDE: "Exclude (hide selected)",
}

# Reverse mapping for internal use
FILTER_MODE_VALUES = {v: k for k, v in FILTER_MODE_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tasmanian Transport filter mode select."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    stop_id = config_entry.data[CONF_STOP_ID]
    stop_name = config_entry.data[CONF_STOP_NAME]

    select_entity = TasTransitFilterModeSelect(
        coordinator=coordinator,
        config_entry=config_entry,
        stop_id=stop_id,
        stop_name=stop_name,
    )

    async_add_entities([select_entity])


class TasTransitFilterModeSelect(SelectEntity, RestoreEntity):
    """Select entity for choosing filter mode (Include/Exclude)."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
    ) -> None:
        """Initialize the filter mode select."""
        self.coordinator = coordinator
        self._config_entry = config_entry
        self._stop_id = stop_id
        self._stop_name = stop_name

        self._attr_unique_id = f"{config_entry.entry_id}_{stop_id}_filter_mode"
        self._attr_name = f"{stop_name} Filter Mode"
        self._attr_icon = "mdi:filter"
        self._attr_should_poll = False

        # Set options for the select entity
        self._attr_options = list(FILTER_MODE_OPTIONS.values())

        # Default to Include mode
        self._attr_current_option = FILTER_MODE_OPTIONS[FILTER_MODE_INCLUDE]

        # Set up device info - attach to bus stop device
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"stop_{stop_id}")},
            "name": f"{stop_name}",
            "manufacturer": "Tasmanian Government",
            "model": "Bus Stop",
            "suggested_area": "Transport",
        }

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option in self._attr_options:
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.debug("Filter mode changed to: %s", option)
        else:
            _LOGGER.warning("Invalid filter mode option: %s", option)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        # Convert user-friendly option back to internal value
        internal_value = FILTER_MODE_VALUES.get(self._attr_current_option)

        return {
            "filter_mode_internal": internal_value,
            "description": self._get_mode_description(),
        }

    def _get_mode_description(self) -> str:
        """Get description of current filter mode."""
        internal_value = FILTER_MODE_VALUES.get(self._attr_current_option)

        if internal_value == FILTER_MODE_INCLUDE:
            return "Only show buses for routes/destinations with enabled filter switches"
        elif internal_value == FILTER_MODE_EXCLUDE:
            return "Hide buses for routes/destinations with enabled filter switches"
        else:
            return "Unknown filter mode"

    def get_internal_filter_mode(self) -> str:
        """Get the internal filter mode value (include/exclude).

        Returns:
            FILTER_MODE_INCLUDE or FILTER_MODE_EXCLUDE
        """
        return FILTER_MODE_VALUES.get(
            self._attr_current_option, FILTER_MODE_INCLUDE
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, restore state."""
        await super().async_added_to_hass()

        # Restore previous state if available
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            _LOGGER.debug("Restored filter mode: %s", self._attr_current_option)
        else:
            # Default to Include mode
            self._attr_current_option = FILTER_MODE_OPTIONS[FILTER_MODE_INCLUDE]
            _LOGGER.debug("New filter mode select, defaulting to Include")
