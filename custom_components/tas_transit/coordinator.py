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
    DOMAIN,
    FILTER_MODE_EXCLUDE,
    FILTER_MODE_INCLUDE,
    UPDATE_INTERVAL_DEFAULT,
    UPDATE_INTERVAL_FREQUENT,
    UPDATE_INTERVAL_THRESHOLD,
)
from .vehicle import Vehicle, VehicleManager
from .websocket_client import TasTransitWebSocketClient
from .shapes import ShapeManager

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

        # Route shapes management
        self.shape_manager = ShapeManager(self.api)
        self._shapes_initialized = False

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        try:
            # Initialize shapes data if not already done
            if not self._shapes_initialized:
                await self._initialize_shapes_data()

            # Start WebSocket client if not already started
            if not self._websocket_started:
                await self._start_websocket_client()

            stop_id = self.config_entry.data[CONF_STOP_ID]
            _LOGGER.debug("Fetching departures for stop %s", stop_id)
            departures = await self.api.get_stop_departures(stop_id)
            _LOGGER.debug("Received %d departures for stop %s", len(departures), stop_id)

            enriched_departures = departures

            # Process the departures data for this stop with filters
            # Use options if available, otherwise fall back to data (initial setup)
            filter_config = self.config_entry.options if self.config_entry.options else self.config_entry.data
            processed_data = self._process_departures(enriched_departures, filter_config)

            # Add vehicle tracking data to processed data
            processed_data["vehicles"] = self._get_vehicles_for_stop(stop_id, processed_data.get("departures", []))

            # Fetch stop location data from currentstopschedule API
            stop_location_data = await self._get_stop_location(stop_id)
            if stop_location_data:
                processed_data["stop_location"] = stop_location_data

            _LOGGER.debug("Processed data for stop %s: next_departure=%s, time_to_departure=%s, vehicles=%d, has_location=%s",
                         stop_id, processed_data.get("next_departure") is not None, processed_data.get("time_to_departure"),
                         len(processed_data.get("vehicles", [])), processed_data.get("stop_location") is not None)

            # Track the earliest departure
            time_to_departure = processed_data.get("time_to_departure")

            # Schedule next update based on closest departure
            await self._schedule_next_update(time_to_departure)

            # Mark vehicles as completed if they no longer appear in API data
            self._mark_missing_vehicles_as_completed({stop_id: processed_data})

            # Remove vehicles that don't match configured route filters
            self._remove_unfiltered_vehicles()

            # Clean up expired vehicles
            removed_count = self.vehicle_manager.cleanup_expired_vehicles()
            if removed_count > 0:
                _LOGGER.debug("Cleaned up %d expired vehicles", removed_count)

            # Update vehicle entity tracking
            await self._update_vehicle_entities()

            return {stop_id: processed_data}

        except TasTransitApiError as err:
            _LOGGER.error("API error: %s", err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    def _apply_filters(self, departures: list[dict[str, Any]], options: dict[str, Any]) -> list[dict[str, Any]]:
        """Apply line number and destination filters to departures."""
        line_filters = options.get(CONF_LINE_FILTERS, [])
        destination_filters = options.get(CONF_DESTINATION_FILTERS, [])
        filter_mode = options.get(CONF_FILTER_MODE, FILTER_MODE_INCLUDE)

        if not line_filters and not destination_filters:
            return departures

        original_count = len(departures)
        filtered_departures = []

        for departure in departures:
            line_number = departure.get("lineNumber", "").strip()
            destination = departure.get("destinationName", "").strip()

            line_match = self._matches_line_filter(line_number, line_filters)
            destination_match = self._matches_destination_filter(destination, destination_filters)

            if filter_mode == FILTER_MODE_INCLUDE:
                if line_filters and not destination_filters:
                    should_include = line_match
                elif not line_filters and destination_filters:
                    should_include = destination_match
                else:
                    should_include = line_match or destination_match
            else:  # FILTER_MODE_EXCLUDE
                if line_filters and not destination_filters:
                    should_include = not line_match
                elif not line_filters and destination_filters:
                    should_include = not destination_match
                else:
                    should_include = not (line_match or destination_match)

            if should_include:
                filtered_departures.append(departure)

        _LOGGER.debug("Filtered %%d departures down to %%d", original_count, len(filtered_departures))
        return filtered_departures

    def _matches_line_filter(self, line_number: str, line_filters: list[str]) -> bool:
        """Check if line number matches any of the line filters."""
        if not line_filters:
            return False

        line_number_lower = line_number.lower()
        for filter_line in line_filters:
            filter_line_lower = filter_line.strip().lower()
            # Exact match only (case insensitive)
            if line_number_lower == filter_line_lower:
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

    def _process_departures(self, departures: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
        """Process departure data with optional filtering."""
        now = datetime.now()

        _LOGGER.debug("Processing %d raw departures", len(departures))

        # Apply filters if configured
        filtered_departures = self._apply_filters(departures, options)
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

            # Include if not cancelled and has future departure time (up to 10 mins ago)
            if not cancelled and minutes_until is not None and minutes_until >= -10:
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
        if min_time_to_departure is not None and -10 <= min_time_to_departure <= UPDATE_INTERVAL_THRESHOLD:
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
        """Start the WebSocket client and subscribe to the configured stop."""
        _LOGGER.info("Starting WebSocket client for real-time vehicle tracking")

        # Start the WebSocket client
        await self.websocket_client.start()

        # Subscribe to the configured stop
        stop_id = self.config_entry.data[CONF_STOP_ID]
        await self.websocket_client.subscribe_to_stop(stop_id)
        _LOGGER.debug("Subscribed to WebSocket updates for stop %s", stop_id)

        self._websocket_started = True

    async def _initialize_shapes_data(self) -> None:
        """Initialize route shapes data manager."""
        try:
            _LOGGER.info("Initializing route shapes data")
            success = await self.shape_manager.initialize()
            if success:
                _LOGGER.info("Route shapes data initialized successfully")
                self._shapes_initialized = True
            else:
                _LOGGER.warning("Route shapes data initialization failed - continuing without shapes features")
                self._shapes_initialized = False
        except Exception as err:
            _LOGGER.error("Error initializing route shapes data: %s", err)
            self._shapes_initialized = False


    def get_active_route_shapes(self) -> dict[str, dict[str, Any]]:
        """Get route shape data for active routes.

        Returns:
            Dict mapping route segment to visualization data
        """
        if not self.shape_manager.is_data_available:
            return {}

        # Get all route segments as GeoJSON features
        route_segments = self.shape_manager.get_all_route_segments()

        # Convert to the expected format
        shapes_data = {}

        for i, segment in enumerate(route_segments):
            segment_id = f"segment_{i}_{segment['properties']['start_stop_id']}_{segment['properties']['end_stop_id']}"

            shapes_data[segment_id] = {
                "coordinates": segment["geometry"]["coordinates"],
                "start_stop_id": segment["properties"]["start_stop_id"],
                "end_stop_id": segment["properties"]["end_stop_id"],
                "start_stop_name": segment["properties"]["start_stop_name"],
                "end_stop_name": segment["properties"]["end_stop_name"],
                "bounds": self.shape_manager.get_route_bounds_for_stops(
                    segment["properties"]["start_stop_id"],
                    segment["properties"]["end_stop_id"]
                ),
            }

        return shapes_data

    def _vehicle_matches_filters(self, vehicle_data: dict[str, Any]) -> bool:
        """Check if a vehicle's route matches the configured line/destination filters.

        If no filters are configured, all vehicles match.
        """
        filter_config = self.config_entry.options if self.config_entry.options else self.config_entry.data
        line_filters = filter_config.get(CONF_LINE_FILTERS, [])
        destination_filters = filter_config.get(CONF_DESTINATION_FILTERS, [])
        filter_mode = filter_config.get(CONF_FILTER_MODE, FILTER_MODE_INCLUDE)

        if not line_filters and not destination_filters:
            return True

        line_number = (vehicle_data.get("lineNumber") or "").strip()

        if filter_mode == FILTER_MODE_INCLUDE:
            # In include mode, vehicle must match at least one line filter
            # (We can't check destination filters here since WebSocket data
            # doesn't include destination info — but line filtering catches most cases)
            if line_filters:
                return self._matches_line_filter(line_number, line_filters)
            # If only destination filters are set, we can't filter WebSocket vehicles
            # by destination, so allow them through
            return True
        else:
            # In exclude mode, vehicle must NOT match any line filter
            if line_filters:
                return not self._matches_line_filter(line_number, line_filters)
            return True

    def _handle_vehicle_update(self, vehicle_data: dict[str, Any]) -> None:
        """Handle vehicle update from WebSocket."""
        # Filter out vehicles on routes we don't care about
        if not self._vehicle_matches_filters(vehicle_data):
            _LOGGER.debug(
                "Ignoring vehicle %s on route %s (doesn't match filters)",
                vehicle_data.get("vehicleId"), vehicle_data.get("lineNumber"),
            )
            return

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
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers import device_registry as dr

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

        # Clean up tracking sets for vehicles that are removed from the manager
        # Get all vehicles (including inactive ones) to determine which have been truly removed
        all_vehicle_ids = set(self.vehicle_manager.get_all_vehicles().keys())

        # Clean up tracker entities for removed vehicles
        removed_vehicles = self._tracked_vehicle_entities - all_vehicle_ids
        if removed_vehicles:
            _LOGGER.info("Removing vehicle entities for %d vehicles: %s", len(removed_vehicles), removed_vehicles)

            # Get entity registry and device registry
            entity_registry = er.async_get(self.hass)
            device_registry = dr.async_get(self.hass)

            # Remove device tracker entities from registry
            # Need to search by pattern since unique_id includes route/trip info
            for vehicle_id in removed_vehicles:
                # Find entities for this vehicle by searching all entities
                entities_to_remove = []
                for entity_entry in entity_registry.entities.values():
                    if (entity_entry.platform == "tas_transit" and
                        entity_entry.domain == "device_tracker" and
                        f"_vehicle_{vehicle_id}" in entity_entry.unique_id):
                        entities_to_remove.append(entity_entry.entity_id)

                # Remove found entities
                for entity_id in entities_to_remove:
                    _LOGGER.debug("Removing device_tracker entity %s for vehicle %s", entity_id, vehicle_id)
                    entity_registry.async_remove(entity_id)

            self._tracked_vehicle_entities -= removed_vehicles

        # Clean up sensor tracking for removed vehicles
        removed_sensor_vehicles = self._tracked_vehicle_sensors - all_vehicle_ids
        if removed_sensor_vehicles:
            _LOGGER.info("Removing vehicle sensor entities for %d vehicles: %s", len(removed_sensor_vehicles), removed_sensor_vehicles)

            # Get entity registry
            entity_registry = er.async_get(self.hass)

            # Remove sensor entities from registry
            for vehicle_id in removed_sensor_vehicles:
                # Find entities for this vehicle by searching all entities
                entities_to_remove = []
                for entity_entry in entity_registry.entities.values():
                    if (entity_entry.platform == "tas_transit" and
                        entity_entry.domain == "sensor" and
                        f"_vehicle_{vehicle_id}_" in entity_entry.unique_id):
                        entities_to_remove.append(entity_entry.entity_id)

                # Remove found entities
                for entity_id in entities_to_remove:
                    _LOGGER.debug("Removing sensor entity %s for vehicle %s", entity_id, vehicle_id)
                    entity_registry.async_remove(entity_id)

            self._tracked_vehicle_sensors -= removed_sensor_vehicles

        # Schedule device cleanup for next tick to ensure entity registry is updated
        all_removed_vehicle_ids = removed_vehicles | removed_sensor_vehicles
        if all_removed_vehicle_ids:
            # Schedule cleanup on next event loop to give entity registry time to update
            self.hass.async_create_task(
                self._cleanup_vehicle_devices(all_removed_vehicle_ids)
            )

    async def _cleanup_vehicle_devices(self, vehicle_ids: set[str]) -> None:
        """Clean up empty vehicle devices after entity removal.

        This is called asynchronously to ensure entity registry has time to update.
        """
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers import device_registry as dr

        # Small delay to ensure entity registry is updated
        await asyncio.sleep(0.1)

        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)

        for vehicle_id in vehicle_ids:
            # Find the device for this vehicle
            device_identifier = (DOMAIN, f"vehicle_{vehicle_id}")
            device_entry = device_registry.async_get_device(identifiers={device_identifier})

            if device_entry:
                # Check if device has any remaining entities
                remaining_entities = er.async_entries_for_device(
                    entity_registry, device_entry.id, include_disabled_entities=True
                )

                if not remaining_entities:
                    _LOGGER.info("Removing empty device for vehicle %s (device_id: %s)", vehicle_id, device_entry.id)
                    device_registry.async_remove_device(device_entry.id)
                else:
                    _LOGGER.debug("Device for vehicle %s still has %d entities, not removing: %s",
                                vehicle_id, len(remaining_entities),
                                [e.entity_id for e in remaining_entities])

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        _LOGGER.info("Shutting down Tasmanian Transport coordinator")

        # Stop WebSocket client
        if hasattr(self, 'websocket_client'):
            await self.websocket_client.disconnect()

        # Shape manager doesn't need explicit cleanup as it uses the API session

        # Close API session
        await self.api.close()

    def _mark_missing_vehicles_as_completed(self, stops_data: dict[str, Any]) -> None:
        """Mark vehicles as departed/completed if they no longer appear in API data."""
        # Collect all active trip IDs from current API responses
        active_trip_ids: set[str] = set()

        for stop_data in stops_data.values():
            departures = stop_data.get("departures", [])
            for departure in departures:
                trip_id = departure.get("tripId")
                if trip_id:
                    active_trip_ids.add(trip_id)

        # First, mark vehicles as departed (sets departure time and calculates destination arrival)
        # This happens when a bus leaves the monitored stop
        departed_count = self.vehicle_manager.mark_vehicles_departed_from_stop(active_trip_ids)
        if departed_count > 0:
            _LOGGER.debug("Marked %d vehicles as departed from monitored stop", departed_count)

        # Then mark vehicles as completed (fallback for vehicles without trip duration)
        self.vehicle_manager.mark_vehicles_not_in_api_as_completed(active_trip_ids)

    def _remove_unfiltered_vehicles(self) -> None:
        """Remove tracked vehicles whose route doesn't match configured filters.

        This catches vehicles that were created before filtering was added,
        or whose lineNumber wasn't available on the first WebSocket message.
        """
        filter_config = self.config_entry.options if self.config_entry.options else self.config_entry.data
        line_filters = filter_config.get(CONF_LINE_FILTERS, [])
        filter_mode = filter_config.get(CONF_FILTER_MODE, FILTER_MODE_INCLUDE)

        if not line_filters:
            return

        vehicles_to_remove = []
        for vehicle_id, vehicle in self.vehicle_manager.get_all_vehicles().items():
            line_number = (vehicle.line_number or "").strip()
            if not line_number:
                # No line number yet — skip, don't remove
                continue

            matches = self._matches_line_filter(line_number, line_filters)
            if filter_mode == FILTER_MODE_INCLUDE and not matches:
                vehicles_to_remove.append(vehicle_id)
            elif filter_mode == FILTER_MODE_EXCLUDE and matches:
                vehicles_to_remove.append(vehicle_id)

        for vehicle_id in vehicles_to_remove:
            _LOGGER.info(
                "Removing vehicle %s (route %s doesn't match filters)",
                vehicle_id, self.vehicle_manager.get_vehicle(vehicle_id).line_number if self.vehicle_manager.get_vehicle(vehicle_id) else "?",
            )
            self.vehicle_manager.remove_vehicle(vehicle_id)