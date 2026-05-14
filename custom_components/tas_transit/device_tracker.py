"""Device tracker platform for Tasmanian Transport buses."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
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
    """Set up device tracker platform for bus stops and vehicles."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    # Track active entities for removal
    coordinator._device_tracker_entities = {}

    # Create device tracker for the configured bus stop
    from .const import CONF_STOP_ID, CONF_STOP_NAME
    stop_id = config_entry.data[CONF_STOP_ID]
    stop_name = config_entry.data[CONF_STOP_NAME]
    
    stop_tracker = TasTransitStopTracker(
        coordinator=coordinator,
        config_entry=config_entry,
        stop_id=stop_id,
        stop_name=stop_name,
    )
    
    async_add_entities([stop_tracker])
    _LOGGER.debug("Added bus stop tracker for %s", stop_id)

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

    async def async_remove(self, *, force_remove: bool = False) -> None:
        """Remove entity from Home Assistant.

        This is called by Home Assistant when the entity should be removed.
        """
        _LOGGER.info("Removing vehicle tracker entity for %s", self._vehicle_id)

        # Clean up entity reference in coordinator
        if hasattr(self.coordinator, '_device_tracker_entities'):
            self.coordinator._device_tracker_entities.pop(self._vehicle_id, None)

        await super().async_remove(force_remove=force_remove)


class TasTransitStopTracker(CoordinatorEntity, TrackerEntity):
    """Device tracker for a bus stop location."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
    ) -> None:
        """Initialize the stop tracker."""
        super().__init__(coordinator)

        self._config_entry = config_entry
        self._stop_id = stop_id
        self._stop_name = stop_name

        self._attr_unique_id = f"{config_entry.entry_id}_stop_{stop_id}"
        self._attr_should_poll = False

        # Set up device info - using stop_ prefix for consistency
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"stop_{stop_id}")},
            "name": f"{stop_name}",
            "manufacturer": "Tasmanian Government",
            "model": "Bus Stop",
            "suggested_area": "Transport",
        }

    @property
    def name(self) -> str:
        """Return the name of the tracker."""
        return f"{self._stop_name}"

    @property
    def source_type(self) -> SourceType:
        """Return the source type of the tracker."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return the latitude of the stop."""
        stop_data = self._get_stop_data()
        if stop_data and stop_data.get("stop_location"):
            return stop_data["stop_location"].get("latitude")
        return None

    @property
    def longitude(self) -> float | None:
        """Return the longitude of the stop."""
        stop_data = self._get_stop_data()
        if stop_data and stop_data.get("stop_location"):
            return stop_data["stop_location"].get("longitude")
        return None

    @property
    def location_accuracy(self) -> int:
        """Return the location accuracy radius in meters."""
        # Bus stop locations are generally accurate
        return 5

    @property
    def icon(self) -> str:
        """Return the icon for the tracker."""
        return "mdi:bus-stop"

    @property
    def available(self) -> bool:
        """Return if the stop tracker is available."""
        stop_data = self._get_stop_data()
        return (
            stop_data is not None 
            and stop_data.get("stop_location") is not None
            and stop_data["stop_location"].get("latitude") is not None
            and stop_data["stop_location"].get("longitude") is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        stop_data = self._get_stop_data()
        if not stop_data:
            return {"stop_id": self._stop_id}

        attrs = {"stop_id": self._stop_id}
        
        # Add stop information if available
        if stop_data.get("stop_location"):
            location_data = stop_data["stop_location"]
            attrs.update({
                "stop_code": location_data.get("code", ""),
                "stop_zone": location_data.get("zone", ""),
                "stop_platform_code": location_data.get("platform_code", ""),
                "parent_station": location_data.get("parent_station", ""),
            })

        # Add transit information for easy access from map
        if stop_data.get("next_departure"):
            next_departure = stop_data["next_departure"]
            attrs.update({
                "next_route": next_departure.get("lineNumber", "Unknown"),
                "next_destination": next_departure.get("destinationName", "Unknown"),
                "trip_id": next_departure.get("tripId", "Unknown"),
                "platform_code": next_departure.get("platformCode", "Unknown"),
                "cancelled": next_departure.get("cancelled", False),
                "scheduled_minutes_until": next_departure.get("scheduledMinutesUntilDeparture"),
                "estimated_minutes_until": next_departure.get("estimatedMinutesUntilDeparture"),
            })
        else:
            attrs.update({
                "next_route": "No service",
                "next_destination": "No service", 
                "scheduled_minutes_until": None,
                "estimated_minutes_until": None,
            })

        # Add time to next bus as primary attribute
        time_to_departure = stop_data.get("time_to_departure")
        if time_to_departure is not None:
            attrs["time_to_next_bus"] = time_to_departure
            attrs["time_to_next_bus_text"] = f"{time_to_departure} min" if time_to_departure > 0 else "Due now"
        else:
            attrs["time_to_next_bus"] = None
            attrs["time_to_next_bus_text"] = "No service"

        # Add vehicle tracking information if available
        vehicles = stop_data.get("vehicles", [])
        attrs["vehicle_count"] = len(vehicles)
        if vehicles:
            attrs["tracked_vehicles"] = [v.get("vehicle_id") for v in vehicles]

        return attrs

    def _get_stop_data(self) -> dict[str, Any] | None:
        """Get the stop data from coordinator."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._stop_id)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        _LOGGER.info("Added bus stop tracker for %s", self._stop_id)


