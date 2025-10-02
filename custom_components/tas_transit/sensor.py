"""Sensor platform for Tasmanian Transport integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DESTINATION_FILTERS,
    CONF_FILTER_MODE,
    CONF_LINE_FILTERS,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
)
from .coordinator import TasTransitDataUpdateCoordinator
from .vehicle_sensors import TasTransitVehicleLineNumberSensor, TasTransitVehicleRealtimeStatusSensor

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tasmanian Transport sensor platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    
    stop_id = config_entry.data[CONF_STOP_ID]
    stop_name = config_entry.data[CONF_STOP_NAME]
    
    sensors = [
        TasTransitNextRouteSensor(coordinator, config_entry, stop_id, stop_name),
        TasTransitNextDestinationSensor(coordinator, config_entry, stop_id, stop_name),
        TasTransitTimeToNextBusSensor(coordinator, config_entry, stop_id, stop_name),
    ]
    
    async_add_entities(sensors)
    
    # Set up vehicle sensor callback for dynamic vehicle sensor creation
    coordinator.set_vehicle_sensor_callback(
        lambda vehicles: _async_add_vehicle_sensors(async_add_entities, coordinator, config_entry, vehicles)
    )


def _async_add_vehicle_sensors(
    async_add_entities: AddEntitiesCallback,
    coordinator: TasTransitDataUpdateCoordinator,
    config_entry: ConfigEntry,
    vehicles: list[Vehicle],
) -> None:
    """Add new vehicle sensor entities."""
    entities = []

    for vehicle in vehicles:
        entities.extend([
            TasTransitVehicleLineNumberSensor(
                coordinator=coordinator,
                config_entry=config_entry,
                vehicle_id=vehicle.vehicle_id,
            ),
            TasTransitVehicleRealtimeStatusSensor(
                coordinator=coordinator,
                config_entry=config_entry,
                vehicle_id=vehicle.vehicle_id,
            ),
        ])
        _LOGGER.debug("Adding vehicle sensors for %s", vehicle.vehicle_id)

    if entities:
        async_add_entities(entities)


class TasTransitSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Tasmanian Transport sensors."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
        sensor_type: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self.stop_id = stop_id
        self.stop_name = stop_name
        self.sensor_type = sensor_type
        self._attr_unique_id = f"{config_entry.entry_id}_{stop_id}_{sensor_type}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"stop_{stop_id}")},
            "name": f"{stop_name}",
            "manufacturer": "Tasmanian Government",
            "model": "Bus Stop",
            "suggested_area": "Transport",
        }
    
    @property
    def stop_data(self) -> dict[str, Any] | None:
        """Get the data for this stop."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.stop_id)

    def _get_filter_attributes(self) -> dict[str, Any]:
        """Get filter-related attributes."""
        options = self.config_entry.options
        if not options:
            return {}
        
        attributes = {}
        
        line_filters = options.get(CONF_LINE_FILTERS, [])
        destination_filters = options.get(CONF_DESTINATION_FILTERS, [])
        filter_mode = options.get(CONF_FILTER_MODE)
        
        if line_filters:
            attributes["route_filters"] = line_filters
            attributes["route_filters_active"] = True
        if destination_filters:
            attributes["destination_filters"] = destination_filters
            attributes["destination_filters_active"] = True
        if filter_mode:
            attributes["filter_type"] = "Show only matching" if filter_mode == "include" else "Hide matching"
            
        # Add summary of active filters
        active_filters = []
        if line_filters:
            active_filters.append(f"Routes: {', '.join(line_filters)}")
        if destination_filters:
            active_filters.append(f"Destinations: {', '.join(destination_filters)}")
        
        if active_filters:
            attributes["active_filters"] = " | ".join(active_filters)
            attributes["filters_enabled"] = True
        else:
            attributes["filters_enabled"] = False
            
        return attributes


class TasTransitNextRouteSensor(TasTransitSensorBase):
    """Sensor for the next bus route (line number)."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
    ) -> None:
        """Initialize the next route sensor."""
        super().__init__(coordinator, config_entry, stop_id, stop_name, "next_route")
        self._attr_name = f"{stop_name} Next Route"
        self._attr_icon = "mdi:routes"


    @property
    def native_value(self) -> str | None:
        """Return the next bus route (line number)."""
        stop_data = self.stop_data
        if not stop_data:
            return None
            
        if not stop_data.get("next_departure"):
            return None
        
        next_departure = stop_data["next_departure"]
        return next_departure.get("lineNumber")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        stop_data = self.stop_data
        
        # Start with basic attributes and filter information
        attributes = {
            "stop_id": self.stop_id,
            **self._get_filter_attributes(),
        }
        
        # Add stop information (no GPS coordinates to avoid appearing on map)
        if stop_data and stop_data.get("stop_location"):
            location_data = stop_data["stop_location"]
            attributes.update({
                "stop_code": location_data.get("code", ""),
                "stop_zone": location_data.get("zone", ""),
                "stop_platform_code": location_data.get("platform_code", ""),
                "parent_station": location_data.get("parent_station", ""),
            })
        
        if not stop_data or not stop_data.get("next_departure"):
            return attributes
        
        next_departure = stop_data["next_departure"]
        scheduled_time = self.coordinator._get_scheduled_time(next_departure)
        estimated_time = self.coordinator._get_estimated_time(next_departure)
        
        attributes.update({
            "line_number": next_departure.get("lineNumber", "Unknown"),
            "destination": next_departure.get("destinationName", "Unknown"),
            "trip_id": next_departure.get("tripId", "Unknown"),
            "platform_code": next_departure.get("platformCode", "Unknown"),
            "scheduled_time": scheduled_time,
            "cancelled": next_departure.get("cancelled", False),
            "scheduled_minutes_until": next_departure.get("scheduledMinutesUntilDeparture"),
            "estimated_minutes_until": next_departure.get("estimatedMinutesUntilDeparture"),
            "all_departures": self._get_all_departures_info(),
            "vehicles": stop_data.get("vehicles", []),
            "vehicle_tracking_enabled": True,
        })
        
        if estimated_time:
            attributes["estimated_time"] = estimated_time
            
        return attributes

    def _get_all_departures_info(self) -> list[dict[str, Any]]:
        """Get information for all upcoming departures."""
        stop_data = self.stop_data
        if not stop_data:
            return []
        
        departures_info = []
        for departure in stop_data.get("departures", []):
            scheduled_time = self.coordinator._get_scheduled_time(departure)
            estimated_time = self.coordinator._get_estimated_time(departure)
            
            info = {
                "line_number": departure.get("lineNumber", "Unknown"),
                "destination": departure.get("destinationName", "Unknown"),
                "scheduled_time": scheduled_time.isoformat() if scheduled_time else None,
                "estimated_time": estimated_time.isoformat() if estimated_time else None,
                "scheduled_minutes_until": departure.get("scheduledMinutesUntilDeparture"),
                "estimated_minutes_until": departure.get("estimatedMinutesUntilDeparture"),
                "cancelled": departure.get("cancelled", False),
                "trip_id": departure.get("tripId"),
                "platform_code": departure.get("platformCode"),
            }
            departures_info.append(info)
        
        return departures_info




class TasTransitNextDestinationSensor(TasTransitSensorBase):
    """Sensor for the next bus destination."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
    ) -> None:
        """Initialize the next destination sensor."""
        super().__init__(coordinator, config_entry, stop_id, stop_name, "next_destination")
        self._attr_name = f"{stop_name} Next Destination"
        self._attr_icon = "mdi:map-marker"


    @property
    def native_value(self) -> str | None:
        """Return the next bus destination."""
        stop_data = self.stop_data
        if not stop_data:
            return None
            
        if not stop_data.get("next_departure"):
            return None
        
        next_departure = stop_data["next_departure"]
        return next_departure.get("destinationName")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        stop_data = self.stop_data
        attributes = {
            "stop_id": self.stop_id,
            **self._get_filter_attributes(),
        }
        
        # Add stop information (no GPS coordinates to avoid appearing on map)
        if stop_data and stop_data.get("stop_location"):
            location_data = stop_data["stop_location"]
            attributes.update({
                "stop_code": location_data.get("code", ""),
                "stop_zone": location_data.get("zone", ""),
                "stop_platform_code": location_data.get("platform_code", ""),
                "parent_station": location_data.get("parent_station", ""),
            })
        
        # Add departure information if available
        if stop_data and stop_data.get("next_departure"):
            next_departure = stop_data["next_departure"]
            attributes.update({
                "line_number": next_departure.get("lineNumber", "Unknown"),
                "trip_id": next_departure.get("tripId", "Unknown"),
                "platform_code": next_departure.get("platformCode", "Unknown"),
                "cancelled": next_departure.get("cancelled", False),
                "scheduled_minutes_until": next_departure.get("scheduledMinutesUntilDeparture"),
                "estimated_minutes_until": next_departure.get("estimatedMinutesUntilDeparture"),
            })
        
        # Add vehicle tracking information if available
        if stop_data:
            attributes.update({
                "vehicles": stop_data.get("vehicles", []),
                "vehicle_tracking_enabled": True,
            })
        
        return attributes


class TasTransitTimeToNextBusSensor(TasTransitSensorBase):
    """Sensor for time until next bus arrival."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        stop_id: str,
        stop_name: str,
    ) -> None:
        """Initialize the time to next bus sensor."""
        super().__init__(coordinator, config_entry, stop_id, stop_name, "time_to_next_bus")
        self._attr_name = f"{stop_name} Time to Next Bus"
        self._attr_native_unit_of_measurement = "min"
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:clock-outline"

    @property
    def native_value(self) -> int | None:
        """Return the time until departure in minutes."""
        stop_data = self.stop_data
        if not stop_data:
            return None
        
        return stop_data.get("time_to_departure")
    
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        stop_data = self.stop_data
        attributes = {
            "stop_id": self.stop_id,
            **self._get_filter_attributes(),
        }
        
        # Add stop information (no GPS coordinates to avoid appearing on map)
        if stop_data and stop_data.get("stop_location"):
            location_data = stop_data["stop_location"]
            attributes.update({
                "stop_code": location_data.get("code", ""),
                "stop_zone": location_data.get("zone", ""),
                "stop_platform_code": location_data.get("platform_code", ""),
                "parent_station": location_data.get("parent_station", ""),
            })
        
        # Add departure information if available
        if stop_data and stop_data.get("next_departure"):
            next_departure = stop_data["next_departure"]
            attributes.update({
                "line_number": next_departure.get("lineNumber", "Unknown"),
                "destination": next_departure.get("destinationName", "Unknown"),
                "trip_id": next_departure.get("tripId", "Unknown"),
                "platform_code": next_departure.get("platformCode", "Unknown"),
                "cancelled": next_departure.get("cancelled", False),
                "scheduled_minutes_until": next_departure.get("scheduledMinutesUntilDeparture"),
                "estimated_minutes_until": next_departure.get("estimatedMinutesUntilDeparture"),
            })
            
        
        # Add vehicle tracking information if available
        if stop_data:
            attributes.update({
                "vehicles": stop_data.get("vehicles", []),
                "vehicle_tracking_enabled": True,
            })
        
        # Add route visualization data if available
        if hasattr(self.coordinator, 'get_active_route_shapes'):
            try:
                route_shapes = self.coordinator.get_active_route_shapes()
                if route_shapes:
                    attributes["route_shapes"] = route_shapes
                    attributes["route_shapes_count"] = len(route_shapes)
            except Exception as err:
                # Don't fail sensor update if route shapes can't be loaded
                _LOGGER.debug("Could not load route shapes: %s", err)

        # Add route shapes availability info
        if hasattr(self.coordinator, 'shape_manager'):
            attributes["shapes_available"] = self.coordinator.shape_manager.is_data_available
        
        return attributes



