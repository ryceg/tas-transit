"""Data update coordinator for Tasmanian Transport integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TasTransitApi, TasTransitApiError
from .const import (
    CONF_DESTINATION_FILTERS,
    CONF_FILTER_MODE,
    CONF_LINE_FILTERS,
    CONF_STOP_ID,
    CONF_STOPS,
    FILTER_MODE_EXCLUDE,
    FILTER_MODE_INCLUDE,
    UPDATE_INTERVAL_DEFAULT,
    UPDATE_INTERVAL_FREQUENT,
    UPDATE_INTERVAL_THRESHOLD,
)
from .vehicle import Vehicle, VehicleManager
from .websocket_client import TasTransitWebSocketClient

_LOGGER = logging.getLogger(__name__)


class TasTransitDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Tasmanian Transport API."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        name: str,
        update_interval: timedelta,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass=hass,
            logger=logger,
            name=name,
            update_interval=update_interval,
        )
        self.config_entry = config_entry
        self.api = TasTransitApi()
        self._current_interval = UPDATE_INTERVAL_DEFAULT
        
        # WebSocket and vehicle tracking
        self.vehicle_manager = VehicleManager()
        self.websocket_client = TasTransitWebSocketClient(self._handle_vehicle_update)
        self._vehicle_entity_callback: Callable[[list[Vehicle]], None] | None = None
        self._vehicle_sensor_callback: Callable[[list[Vehicle]], None] | None = None
        self._tracked_vehicle_entities: set[str] = set()
        self._tracked_vehicle_sensors: set[str] = set()
        self._websocket_started = False

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        try:
            # Start WebSocket client if not already started
            if not self._websocket_started:
                await self._start_websocket_client()
            
            stops_data = {}
            min_time_to_departure = None
            
            # Process each configured stop
            for stop_config in self.config_entry.data[CONF_STOPS]:
                stop_id = stop_config[CONF_STOP_ID]
                _LOGGER.debug("Fetching departures for stop %s", stop_id)
                departures = await self.api.get_stop_departures(stop_id)
                _LOGGER.debug("Received %d departures for stop %s", len(departures), stop_id)
                
                # Process the departures data for this stop with filters
                processed_data = self._process_departures(departures, stop_config)
                
                # Add vehicle tracking data to processed data
                processed_data["vehicles"] = self._get_vehicles_for_stop(stop_id, processed_data.get("departures", []))
                
                # Fetch stop location data from currentstopschedule API
                stop_location_data = await self._get_stop_location(stop_id)
                if stop_location_data:
                    processed_data["stop_location"] = stop_location_data
                
                stops_data[stop_id] = processed_data
                _LOGGER.debug("Processed data for stop %s: next_departure=%s, time_to_departure=%s, vehicles=%d, has_location=%s", 
                             stop_id, processed_data.get("next_departure") is not None, processed_data.get("time_to_departure"),
                             len(processed_data.get("vehicles", [])), processed_data.get("stop_location") is not None)
                
                # Track the earliest departure across all stops
                time_to_departure = processed_data.get("time_to_departure")
                if time_to_departure is not None:
                    if min_time_to_departure is None or time_to_departure < min_time_to_departure:
                        min_time_to_departure = time_to_departure
            
            # Schedule next update based on closest departure
            await self._schedule_next_update(min_time_to_departure)
            
            # Update vehicle entity tracking
            await self._update_vehicle_entities()
            
            return stops_data

        except TasTransitApiError as err:
            _LOGGER.error("API error: %s", err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    def _apply_filters(self, departures: list[dict[str, Any]], stop_config: dict[str, Any]) -> list[dict[str, Any]]:
        """Apply line number and destination filters to departures."""
        line_filters = stop_config.get(CONF_LINE_FILTERS, [])
        destination_filters = stop_config.get(CONF_DESTINATION_FILTERS, [])
        filter_mode = stop_config.get(CONF_FILTER_MODE, FILTER_MODE_INCLUDE)
        
        # If no filters configured, return all departures
        if not line_filters and not destination_filters:
            return departures
        
        filtered_departures = []
        
        for departure in departures:
            line_number = departure.get("lineNumber", "").strip()
            destination = departure.get("destinationName", "").strip()
            
            # Check if departure matches filters
            line_match = self._matches_line_filter(line_number, line_filters)
            destination_match = self._matches_destination_filter(destination, destination_filters)
            
            # Determine if departure should be included
            if filter_mode == FILTER_MODE_INCLUDE:
                # Include if matches any line filter OR any destination filter (when filters are provided)
                should_include = False
                if line_filters and line_match:
                    should_include = True
                if destination_filters and destination_match:
                    should_include = True
                # If only one type of filter is configured, only check that type
                if line_filters and not destination_filters:
                    should_include = line_match
                elif destination_filters and not line_filters:
                    should_include = destination_match
            else:  # FILTER_MODE_EXCLUDE
                # Exclude if matches any line filter OR any destination filter
                should_exclude = False
                if line_filters and line_match:
                    should_exclude = True
                if destination_filters and destination_match:
                    should_exclude = True
                should_include = not should_exclude
            
            if should_include:
                filtered_departures.append(departure)
                _LOGGER.debug("Including departure: line=%s, dest=%s", line_number, destination)
            else:
                _LOGGER.debug("Filtering out departure: line=%s, dest=%s", line_number, destination)
        
        return filtered_departures

    def _matches_line_filter(self, line_number: str, line_filters: list[str]) -> bool:
        """Check if line number matches any of the line filters."""
        if not line_filters:
            return False
        
        line_number_lower = line_number.lower()
        for filter_line in line_filters:
            filter_line_lower = filter_line.strip().lower()
            # Exact match or partial match (e.g., "58" matches "X58")
            if (line_number_lower == filter_line_lower or 
                filter_line_lower in line_number_lower or
                line_number_lower in filter_line_lower):
                return True
        return False

    def _matches_destination_filter(self, destination: str, destination_filters: list[str]) -> bool:
        """Check if destination matches any of the destination filters."""
        if not destination_filters:
            return False
        
        destination_lower = destination.lower()
        for filter_dest in destination_filters:
            filter_dest_lower = filter_dest.strip().lower()
            # Case-insensitive partial match
            if filter_dest_lower in destination_lower:
                return True
        return False

    def _process_departures(self, departures: list[dict[str, Any]], stop_config: dict[str, Any]) -> dict[str, Any]:
        """Process departure data with optional filtering."""
        now = datetime.now()
        
        _LOGGER.debug("Processing %d raw departures", len(departures))
        
        # Apply filters if configured
        filtered_departures = self._apply_filters(departures, stop_config)
        _LOGGER.debug("After filtering: %d departures remaining", len(filtered_departures))
        
        # Filter departures to only include upcoming ones (non-cancelled, positive minutes)
        upcoming_departures = []
        for departure in filtered_departures:
            # Use estimated minutes if available, otherwise scheduled minutes
            minutes_until = departure.get("estimatedMinutesUntilDeparture")
            if minutes_until is None:
                minutes_until = departure.get("scheduledMinutesUntilDeparture")
            
            cancelled = departure.get("cancelled", False)
            _LOGGER.debug("Departure: line=%s, dest=%s, minutes_until=%s, cancelled=%s", 
                         departure.get("lineNumber"), departure.get("destinationName"), minutes_until, cancelled)
            
            # Include if not cancelled and has future departure time
            if not cancelled and minutes_until is not None and minutes_until >= 0:
                upcoming_departures.append(departure)
        
        _LOGGER.debug("Found %d upcoming departures after filtering", len(upcoming_departures))
        
        # Sort by minutes until departure (estimated or scheduled)
        def sort_key(dep):
            est_min = dep.get("estimatedMinutesUntilDeparture")
            if est_min is not None:
                return est_min
            return dep.get("scheduledMinutesUntilDeparture", 999999)
        
        upcoming_departures.sort(key=sort_key)
        
        # Get the next departure
        next_departure = upcoming_departures[0] if upcoming_departures else None
        
        if not next_departure:
            _LOGGER.debug("No upcoming departures found")
            return {
                "next_departure": None,
                "time_to_departure": None,
                "departures": [],
                "last_updated": now,
            }
        
        # Get time to departure (prefer estimated over scheduled)
        time_to_departure = next_departure.get("estimatedMinutesUntilDeparture")
        if time_to_departure is None:
            time_to_departure = next_departure.get("scheduledMinutesUntilDeparture")
        
        _LOGGER.debug("Next departure: line=%s, dest=%s, time_to_departure=%s", 
                     next_departure.get("lineNumber"), next_departure.get("destinationName"), time_to_departure)
        
        return {
            "next_departure": next_departure,
            "time_to_departure": time_to_departure,
            "departures": upcoming_departures,  # Expose all departures for user filtering
            "last_updated": now,
        }

    def _get_scheduled_time(self, departure: dict[str, Any]) -> datetime | None:
        """Extract scheduled departure time from departure data."""
        scheduled_time = departure.get("scheduledDepartureTime")
        if scheduled_time:
            return self.api.parse_departure_time(scheduled_time)
        return None

    def _get_estimated_time(self, departure: dict[str, Any]) -> datetime | None:
        """Extract estimated departure time from departure data."""
        estimated_time = departure.get("estimatedDepartureTime")
        if estimated_time:
            return self.api.parse_departure_time(estimated_time)
        return None


    async def _schedule_next_update(self, min_time_to_departure: int | None) -> None:
        """Adjust update interval based on departure times."""
        # Determine update interval based on closest departure
        if min_time_to_departure is not None and min_time_to_departure <= UPDATE_INTERVAL_THRESHOLD:
            # Bus within threshold - use frequent updates
            interval = UPDATE_INTERVAL_FREQUENT
            self.logger.debug(
                "Bus departure in %d minutes, using %d second updates",
                min_time_to_departure,
                interval
            )
        else:
            # No buses soon - use default interval
            interval = UPDATE_INTERVAL_DEFAULT
            self.logger.debug(
                "No buses within %d minutes, using %d second updates",
                UPDATE_INTERVAL_THRESHOLD,
                interval
            )
        
        # Update interval if changed - coordinator will handle the actual scheduling
        if interval != self._current_interval:
            self._current_interval = interval
            self.update_interval = timedelta(seconds=interval)
            self.logger.debug("Updated coordinator interval to %d seconds", interval)
    
    async def _start_websocket_client(self) -> None:
        """Start the WebSocket client and subscribe to configured stops."""
        _LOGGER.info("Starting WebSocket client for real-time vehicle tracking")
        
        # Start the WebSocket client
        await self.websocket_client.start()
        
        # Subscribe to all configured stops
        for stop_config in self.config_entry.data[CONF_STOPS]:
            stop_id = stop_config[CONF_STOP_ID]
            await self.websocket_client.subscribe_to_stop(stop_id)
            _LOGGER.debug("Subscribed to WebSocket updates for stop %s", stop_id)
        
        self._websocket_started = True

    def _handle_vehicle_update(self, vehicle_data: dict[str, Any]) -> None:
        """Handle vehicle update from WebSocket."""
        vehicle = self.vehicle_manager.update_vehicle(vehicle_data)
        if vehicle:
            # Trigger coordinator update to notify entities
            self.async_set_updated_data(self.data)
            _LOGGER.debug("Updated vehicle %s from WebSocket", vehicle.vehicle_id)

    def _get_vehicles_for_stop(self, stop_id: str, departures: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Get vehicle tracking data for departures at a stop."""
        vehicles = []
        
        # Match vehicles to departures based on trip_id
        trip_ids = {dep.get("tripId") for dep in departures if dep.get("tripId")}
        
        for vehicle in self.vehicle_manager.get_active_vehicles():
            if vehicle.trip_id in trip_ids:
                vehicles.append({
                    "vehicle_id": vehicle.vehicle_id,
                    "trip_id": vehicle.trip_id,
                    "line_number": vehicle.line_number,
                    "location": vehicle.location.to_dict() if vehicle.location else None,
                    "last_updated": vehicle.last_updated.isoformat(),
                })
        
        return vehicles

    async def _get_stop_location(self, stop_id: str) -> dict[str, Any] | None:
        """Get stop location data from currentstopschedule API."""
        try:
            _LOGGER.debug("Fetching stop location for stop %s", stop_id)
            schedule_data = await self.api.get_stop_schedule(stop_id)
            
            if schedule_data and "stop" in schedule_data:
                stop_info = schedule_data["stop"]
                location = stop_info.get("location")
                
                if location and "latitude" in location and "longitude" in location:
                    return {
                        "stop_id": stop_info.get("id", stop_id),
                        "name": stop_info.get("name", "Unknown Stop"),
                        "code": stop_info.get("code", ""),
                        "latitude": location["latitude"],
                        "longitude": location["longitude"],
                        "zone": stop_info.get("zone", ""),
                        "platform_code": stop_info.get("platformCode", ""),
                        "parent_station": stop_info.get("parentStation", ""),
                    }
            
            _LOGGER.warning("No location data found for stop %s", stop_id)
            return None
            
        except TasTransitApiError as err:
            _LOGGER.error("Error fetching stop location for %s: %s", stop_id, err)
            return None
        except Exception as err:
            _LOGGER.error("Unexpected error fetching stop location for %s: %s", stop_id, err)
            return None

    def set_vehicle_entity_callback(self, callback: Callable[[list[Vehicle]], None]) -> None:
        """Set callback for adding new vehicle entities."""
        self._vehicle_entity_callback = callback

    def set_vehicle_sensor_callback(self, callback: Callable[[list[Vehicle]], None]) -> None:
        """Set callback for adding new vehicle sensor entities."""
        self._vehicle_sensor_callback = callback

    async def _update_vehicle_entities(self) -> None:
        """Update vehicle tracker entities and sensors based on active vehicles."""
        active_vehicles = self.vehicle_manager.get_active_vehicles()
        
        # Handle device tracker entities
        if self._vehicle_entity_callback is not None:
            new_vehicles = []
            
            for vehicle in active_vehicles:
                if vehicle.vehicle_id not in self._tracked_vehicle_entities:
                    new_vehicles.append(vehicle)
                    self._tracked_vehicle_entities.add(vehicle.vehicle_id)
            
            if new_vehicles:
                _LOGGER.debug("Adding %d new vehicle tracker entities", len(new_vehicles))
                self._vehicle_entity_callback(new_vehicles)
        
        # Handle vehicle sensor entities
        if self._vehicle_sensor_callback is not None:
            new_sensor_vehicles = []
            
            for vehicle in active_vehicles:
                if vehicle.vehicle_id not in self._tracked_vehicle_sensors:
                    new_sensor_vehicles.append(vehicle)
                    self._tracked_vehicle_sensors.add(vehicle.vehicle_id)
            
            if new_sensor_vehicles:
                _LOGGER.debug("Adding %d new vehicle sensor entities", len(new_sensor_vehicles))
                self._vehicle_sensor_callback(new_sensor_vehicles)
        
        # Clean up entities for vehicles that no longer exist
        all_vehicle_ids = {v.vehicle_id for v in self.vehicle_manager.get_all_vehicles().values()}
        
        # Clean up tracker entities
        removed_vehicles = self._tracked_vehicle_entities - all_vehicle_ids
        if removed_vehicles:
            _LOGGER.debug("Cleaning up %d removed vehicle tracker entities", len(removed_vehicles))
            # Remove entities from Home Assistant
            if hasattr(self, '_device_tracker_entities'):
                for vehicle_id in removed_vehicles:
                    entity = self._device_tracker_entities.get(vehicle_id)
                    if entity:
                        entity.mark_for_removal()
                        self._device_tracker_entities.pop(vehicle_id, None)
            
            self._tracked_vehicle_entities -= removed_vehicles
        
        # Clean up sensor entities
        removed_sensor_vehicles = self._tracked_vehicle_sensors - all_vehicle_ids
        if removed_sensor_vehicles:
            _LOGGER.debug("Cleaning up %d removed vehicle sensor entities", len(removed_sensor_vehicles))
            self._tracked_vehicle_sensors -= removed_sensor_vehicles

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        _LOGGER.info("Shutting down Tasmanian Transport coordinator")
        
        # Stop WebSocket client
        if hasattr(self, 'websocket_client'):
            await self.websocket_client.disconnect()
        
        # Close API session
        await self.api.close()