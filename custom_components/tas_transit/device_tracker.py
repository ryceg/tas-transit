"""Device tracker platform for Tasmanian Transport buses."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TasTransitDataUpdateCoordinator
from .vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device tracker platform for bus vehicles."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    # Track active entities for removal
    coordinator._device_tracker_entities = {}

    # The coordinator will handle adding/removing vehicle trackers dynamically
    # Initial setup creates no entities - they are created when vehicles appear
    _LOGGER.debug("Device tracker platform initialized for entry %s", config_entry.entry_id)

    # Store the callback for adding new vehicle entities
    coordinator.set_vehicle_entity_callback(
        lambda vehicles: _async_add_vehicle_entities(async_add_entities, coordinator, config_entry, vehicles)
    )


def _async_add_vehicle_entities(
    async_add_entities: AddEntitiesCallback,
    coordinator: TasTransitDataUpdateCoordinator,
    config_entry: ConfigEntry,
    vehicles: list[Vehicle],
) -> None:
    """Add new vehicle tracker entities."""
    entities = []

    for vehicle in vehicles:
        entity = TasTransitVehicleTracker(
            coordinator=coordinator,
            config_entry=config_entry,
            vehicle_id=vehicle.vehicle_id,
        )
        entities.append(entity)
        # Track the entity for potential removal
        coordinator._device_tracker_entities[vehicle.vehicle_id] = entity
        _LOGGER.debug("Adding vehicle tracker for %s", vehicle.vehicle_id)

    if entities:
        async_add_entities(entities)


class TasTransitVehicleTracker(CoordinatorEntity, TrackerEntity):
    """Device tracker for a Tasmanian Transport bus."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        vehicle_id: str,
    ) -> None:
        """Initialize the vehicle tracker."""
        super().__init__(coordinator)

        self._config_entry = config_entry
        self._vehicle_id = vehicle_id

        # Create a more unique ID by including trip and route information
        vehicle = self._get_vehicle_initial()
        route_suffix = ""
        if vehicle and vehicle.line_number:
            route_suffix = f"_route_{vehicle.line_number}"
        if vehicle and vehicle.trip_id:
            # Use first 8 chars of trip_id for uniqueness without making ID too long
            route_suffix += f"_{vehicle.trip_id[:8]}"

        self._attr_unique_id = f"{config_entry.entry_id}_vehicle_{vehicle_id}{route_suffix}"
        self._attr_should_poll = False

        # Set up device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"vehicle_{vehicle_id}")},
            "name": f"Bus {vehicle_id}",
            "manufacturer": "Tasmanian Government",
            "model": "Transit Vehicle",
            "via_device": (DOMAIN, f"{config_entry.entry_id}_coordinator"),
        }

    @property
    def name(self) -> str:
        """Return the name of the tracker."""
        vehicle = self._get_vehicle()
        if vehicle and vehicle.line_number:
            return f"Bus {self._vehicle_id} (Route {vehicle.line_number})"
        return f"Bus {self._vehicle_id}"

    @property
    def source_type(self) -> SourceType:
        """Return the source type of the tracker."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return the latitude of the vehicle."""
        vehicle = self._get_vehicle()
        if vehicle and vehicle.location:
            return vehicle.location.latitude
        return None

    @property
    def longitude(self) -> float | None:
        """Return the longitude of the vehicle."""
        vehicle = self._get_vehicle()
        if vehicle and vehicle.location:
            return vehicle.location.longitude
        return None

    @property
    def location_accuracy(self) -> int:
        """Return the location accuracy radius in meters."""
        # GPS accuracy for transit vehicles is typically good
        return 10

    @property
    def icon(self) -> str:
        """Return the icon for the tracker."""
        return "mdi:bus"

    @property
    def available(self) -> bool:
        """Return if the vehicle is available (active and has location)."""
        vehicle = self._get_vehicle()
        return (
            vehicle is not None
            and vehicle.is_active
            and vehicle.location is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        vehicle = self._get_vehicle()
        if not vehicle:
            return {}

        attrs = {
            "vehicle_id": self._vehicle_id,
            "is_active": vehicle.is_active,
            "last_updated": vehicle.last_updated.isoformat(),
        }

        # Add vehicle-specific attributes if available
        if vehicle.trip_id:
            attrs["trip_id"] = vehicle.trip_id
        if vehicle.line_number:
            attrs["line_number"] = vehicle.line_number
            attrs["route"] = vehicle.line_number  # Alternative name for route
        if vehicle.trip_template_id:
            attrs["trip_template_id"] = vehicle.trip_template_id

        # Add location attributes
        if vehicle.location:
            attrs.update({
                "heading": vehicle.location.heading,
                "gps_accuracy": self.location_accuracy,
            })

            # Add bearing/direction text if heading is available
            if vehicle.location.heading is not None:
                attrs["direction"] = self._heading_to_direction(vehicle.location.heading)

        return attrs

    def _get_vehicle(self) -> Vehicle | None:
        """Get the vehicle data from coordinator."""
        if not hasattr(self.coordinator, 'vehicle_manager'):
            return None
        return self.coordinator.vehicle_manager.get_vehicle(self._vehicle_id)

    def _get_vehicle_initial(self) -> Vehicle | None:
        """Get the vehicle data during initialization (may return None if coordinator not ready)."""
        try:
            return self._get_vehicle()
        except (AttributeError, KeyError):
            # During initialization, coordinator may not be fully set up yet
            return None

    def _heading_to_direction(self, heading: float) -> str:
        """Convert heading degrees to cardinal direction."""
        directions = [
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
        ]
        index = round(heading / 22.5) % 16
        return directions[index]

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Check if this vehicle still exists and is active
        vehicle = self._get_vehicle()
        if vehicle is None:
            # Vehicle no longer exists, entity should be removed
            # This will be handled by the coordinator's entity management
            _LOGGER.debug("Vehicle %s no longer exists", self._vehicle_id)
            return

        if not vehicle.is_active:
            # Vehicle is no longer active, but we keep the entity for a while
            # in case it becomes active again
            _LOGGER.debug("Vehicle %s is no longer active", self._vehicle_id)

        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        _LOGGER.info("Added vehicle tracker for %s", self._vehicle_id)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        await super().async_will_remove_from_hass()

        # Clean up entity reference in coordinator
        if hasattr(self.coordinator, '_device_tracker_entities'):
            self.coordinator._device_tracker_entities.pop(self._vehicle_id, None)

        _LOGGER.info("Removed vehicle tracker for %s", self._vehicle_id)

    def mark_for_removal(self) -> None:
        """Mark this entity for removal from Home Assistant."""
        self.async_schedule_update_ha_state()
        # Schedule entity removal
        self.hass.async_create_task(self.async_remove())



