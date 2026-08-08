"""Switch platform for Tasmanian Transport integration filters."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_STOP_ID, CONF_STOP_NAME, DOMAIN
from .coordinator import TasTransitDataUpdateCoordinator
from .filter_registry import RouteDestinationRegistry

_LOGGER = logging.getLogger(__name__)


def _sanitize_entity_id(text: str) -> str:
    """Sanitize text for use in entity ID."""
    # Replace spaces and special characters with underscores
    sanitized = re.sub(r'[^a-z0-9_]', '_', text.lower())
    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    return sanitized.strip('_')


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tasmanian Transport filter switches."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    filter_registry = hass.data[DOMAIN][config_entry.entry_id]["filter_registry"]

    stop_id = config_entry.data[CONF_STOP_ID]
    stop_name = config_entry.data[CONF_STOP_NAME]

    # Store callback for adding new switches when routes/destinations are discovered
    coordinator.set_filter_switch_callback(
        lambda: _async_add_filter_switches(
            async_add_entities, coordinator, config_entry, filter_registry, stop_id, stop_name
        )
    )

    # Create initial switches for any routes/destinations already in the registry
    _async_add_filter_switches(
        async_add_entities, coordinator, config_entry, filter_registry, stop_id, stop_name
    )


def _async_add_filter_switches(
    async_add_entities: AddEntitiesCallback,
    coordinator: TasTransitDataUpdateCoordinator,
    config_entry: ConfigEntry,
    filter_registry: RouteDestinationRegistry,
    stop_id: str,
    stop_name: str,
) -> None:
    """Add filter switch entities for new routes/destinations."""
    entities = []

    # Get existing switch entity IDs to avoid duplicates
    existing_switches = coordinator._filter_switch_entities if hasattr(coordinator, '_filter_switch_entities') else set()

    # Initialize the set if it doesn't exist
    if not hasattr(coordinator, '_filter_switch_entities'):
        coordinator._filter_switch_entities = set()

    # Create switches for routes
    for route_number in filter_registry.get_all_routes().keys():
        entity_id_suffix = f"route_{_sanitize_entity_id(route_number)}"
        if entity_id_suffix not in existing_switches:
            entity = TasTransitRouteFilterSwitch(
                coordinator=coordinator,
                config_entry=config_entry,
                filter_registry=filter_registry,
                stop_id=stop_id,
                stop_name=stop_name,
                route_number=route_number,
            )
            entities.append(entity)
            coordinator._filter_switch_entities.add(entity_id_suffix)
            _LOGGER.debug("Adding route filter switch for %s", route_number)

    # Create switches for destinations
    for dest_name in filter_registry.get_all_destinations().keys():
        entity_id_suffix = f"dest_{_sanitize_entity_id(dest_name)}"
        if entity_id_suffix not in existing_switches:
            entity = TasTransitDestinationFilterSwitch(
                coordinator=coordinator,
                config_entry=config_entry,
                filter_registry=filter_registry,
                stop_id=stop_id,
                stop_name=stop_name,
                destination_name=dest_name,
            )
            entities.append(entity)
            coordinator._filter_switch_entities.add(entity_id_suffix)
            _LOGGER.debug("Adding destination filter switch for %s", dest_name)

    if entities:
        async_add_entities(entities)


class TasTransitFilterSwitchBase(SwitchEntity, RestoreEntity):
    """Base class for Tasmanian Transport filter switches."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        filter_registry: RouteDestinationRegistry,
        stop_id: str,
        stop_name: str,
    ) -> None:
        """Initialize the filter switch."""
        self.coordinator = coordinator
        self._config_entry = config_entry
        self._filter_registry = filter_registry
        self._stop_id = stop_id
        self._stop_name = stop_name
        self._attr_should_poll = False
        self._is_on = False

        # Set up device info - attach to bus stop device
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"stop_{stop_id}")},
            "name": f"{stop_name}",
            "manufacturer": "Tasmanian Government",
            "model": "Bus Stop",
            "suggested_area": "Transport",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the filter is enabled."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the filter on (enable filtering for this route/destination)."""
        self._is_on = True
        self.async_write_ha_state()
        _LOGGER.debug("Turned on filter: %s", self.name)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the filter off (disable filtering for this route/destination)."""
        self._is_on = False
        self.async_write_ha_state()
        _LOGGER.debug("Turned off filter: %s", self.name)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, restore state."""
        await super().async_added_to_hass()

        # Restore previous state if available
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = last_state.state == "on"
            _LOGGER.debug("Restored filter switch %s state: %s", self.name, self._is_on)
        else:
            # For new switches, check if they should be pre-enabled based on config flow selections
            self._is_on = self._should_be_pre_enabled()
            _LOGGER.debug("New filter switch %s, initial state: %s", self.name, self._is_on)

    def _should_be_pre_enabled(self) -> bool:
        """Check if this switch should be pre-enabled based on config flow selections.

        Returns:
            True if this filter was pre-selected during setup, False otherwise
        """
        # This will be overridden in subclasses
        return False


class TasTransitRouteFilterSwitch(TasTransitFilterSwitchBase):
    """Switch to enable/disable filtering for a specific route."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        filter_registry: RouteDestinationRegistry,
        stop_id: str,
        stop_name: str,
        route_number: str,
    ) -> None:
        """Initialize the route filter switch."""
        super().__init__(coordinator, config_entry, filter_registry, stop_id, stop_name)

        self._route_number = route_number
        self._attr_unique_id = f"{config_entry.entry_id}_{stop_id}_route_filter_{_sanitize_entity_id(route_number)}"
        self._attr_name = f"{stop_name} Route {route_number} Filter"
        self._attr_icon = "mdi:bus-side"

    def _should_be_pre_enabled(self) -> bool:
        """Check if this route was pre-selected during setup."""
        pre_selected = self.coordinator.hass.data[DOMAIN][self._config_entry.entry_id].get("pre_selected_routes", [])
        return self._route_number in pre_selected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        route_info = self._filter_registry.get_route(self._route_number)

        attrs = {
            "route_number": self._route_number,
            "filter_type": "route",
        }

        if route_info:
            attrs.update({
                "currently_active": route_info.is_currently_active,
                "first_seen": route_info.first_seen.isoformat(),
                "last_seen": route_info.last_seen.isoformat(),
            })

        return attrs


class TasTransitDestinationFilterSwitch(TasTransitFilterSwitchBase):
    """Switch to enable/disable filtering for a specific destination."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        filter_registry: RouteDestinationRegistry,
        stop_id: str,
        stop_name: str,
        destination_name: str,
    ) -> None:
        """Initialize the destination filter switch."""
        super().__init__(coordinator, config_entry, filter_registry, stop_id, stop_name)

        self._destination_name = destination_name
        self._attr_unique_id = f"{config_entry.entry_id}_{stop_id}_dest_filter_{_sanitize_entity_id(destination_name)}"
        self._attr_name = f"{stop_name} Destination {destination_name} Filter"
        self._attr_icon = "mdi:map-marker"

    def _should_be_pre_enabled(self) -> bool:
        """Check if this destination was pre-selected during setup."""
        pre_selected = self.coordinator.hass.data[DOMAIN][self._config_entry.entry_id].get("pre_selected_destinations", [])
        return self._destination_name in pre_selected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        dest_info = self._filter_registry.get_destination(self._destination_name)

        attrs = {
            "destination_name": self._destination_name,
            "filter_type": "destination",
        }

        if dest_info:
            attrs.update({
                "currently_active": dest_info.is_currently_active,
                "first_seen": dest_info.first_seen.isoformat(),
                "last_seen": dest_info.last_seen.isoformat(),
            })

        return attrs
