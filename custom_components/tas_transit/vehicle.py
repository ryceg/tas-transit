"""Vehicle data models and management for Tasmanian Transport integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Vehicle cleanup settings - using const values
from .const import (
    VEHICLE_INACTIVE_TIMEOUT as INACTIVE_TIMEOUT_SECONDS,
    VEHICLE_CLEANUP_INTERVAL as CLEANUP_INTERVAL_SECONDS,
    VEHICLE_TRIP_COMPLETED_GRACE_PERIOD,
    VEHICLE_STALLED_AT_INDEX_TIMEOUT,
    VEHICLE_FALLBACK_TIMEOUT,
)

VEHICLE_INACTIVE_TIMEOUT = timedelta(seconds=INACTIVE_TIMEOUT_SECONDS)
VEHICLE_CLEANUP_INTERVAL = timedelta(seconds=CLEANUP_INTERVAL_SECONDS)


@dataclass
class VehicleLocation:
    """Vehicle GPS location data."""
    latitude: float
    longitude: float
    heading: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VehicleLocation | None:
        """Create VehicleLocation from API data."""
        if not isinstance(data, dict):
            return None

        location = data.get("location", {})
        if not isinstance(location, dict):
            return None

        try:
            # Preserve high precision coordinates from the API
            latitude = location.get("latitude")
            longitude = location.get("longitude")

            if latitude is None or longitude is None:
                _LOGGER.debug("Missing latitude or longitude in location data")
                return None

            return cls(
                latitude=float(latitude),
                longitude=float(longitude),
                heading=float(data.get("heading")) if data.get("heading") is not None else None,
            )
        except (ValueError, TypeError) as err:
            _LOGGER.debug("Invalid location data: %s", err)
            return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "heading": self.heading,
        }


@dataclass
class Vehicle:
    """Represents a transit vehicle with real-time data."""
    vehicle_id: str
    trip_id: str | None
    line_number: str | None
    trip_template_id: str | None
    location: VehicleLocation | None
    last_updated: datetime
    is_active: bool = True

    # Trip completion tracking
    last_seen_index: int | None = None
    first_seen_at_index: datetime | None = None
    trip_completed_at: datetime | None = None
    marked_for_removal: bool = False

    # Next stop information
    current_stop_name: str | None = None
    next_stop_name: str | None = None
    stops_remaining: str | int | None = None

    def update_from_websocket(self, data: dict[str, Any]) -> bool:
        """Update vehicle data from WebSocket message.

        Args:
            data: WebSocket message data

        Returns:
            True if vehicle was updated, False if data was invalid
        """
        try:
            message_type = data.get("type")

            if message_type == "REMOVED":
                self.is_active = False
                self.location = None
                self.last_updated = datetime.now()

                # Mark trip as completed when vehicle is explicitly removed
                if not self.trip_completed_at:
                    self.trip_completed_at = datetime.now()
                    _LOGGER.info("Vehicle %s trip completed (REMOVED signal)", self.vehicle_id)

                _LOGGER.debug("Vehicle %s marked as removed", self.vehicle_id)
                return True

            elif message_type == "APPROACHING":
                # Update vehicle data
                self.trip_id = data.get("tripId")
                self.line_number = data.get("lineNumber")
                self.trip_template_id = data.get("tripTemplateId")
                self.last_updated = datetime.now()
                self.is_active = True

                # Update location
                location_data = data.get("vehicleLocation")
                if location_data:
                    self.location = VehicleLocation.from_dict(location_data)

                # Track index for trip completion detection
                current_index = data.get("index")
                if current_index is not None:
                    if self.last_seen_index != current_index:
                        self.last_seen_index = current_index
                        self.first_seen_at_index = datetime.now()
                        _LOGGER.debug("Vehicle %s at index %s", self.vehicle_id, current_index)

                _LOGGER.debug("Updated vehicle %s: route %s, location %s",
                            self.vehicle_id, self.line_number,
                            f"{self.location.latitude},{self.location.longitude}" if self.location else "unknown")
                return True

            else:
                _LOGGER.debug("Unknown message type %s for vehicle %s", message_type, self.vehicle_id)
                return False

        except Exception as err:
            _LOGGER.error("Error updating vehicle %s: %s", self.vehicle_id, err)
            return False

    def to_device_tracker_attrs(self) -> dict[str, Any]:
        """Convert to device tracker attributes."""
        attrs = {
            "vehicle_id": self.vehicle_id,
            "trip_id": self.trip_id,
            "line_number": self.line_number,
            "trip_template_id": self.trip_template_id,
            "last_updated": self.last_updated.isoformat(),
            "is_active": self.is_active,

            # Trip completion tracking info
            "last_seen_index": self.last_seen_index,
            "is_trip_completed": self.is_trip_completed(),
            "trip_completed_at": self.trip_completed_at.isoformat() if self.trip_completed_at else None,
            "is_stalled": self.is_stalled_at_index(),

            # Next stop information
            "current_stop_name": self.current_stop_name,
            "next_stop_name": self.next_stop_name,
            "stops_remaining": self.stops_remaining,
        }

        if self.location:
            attrs.update({
                "latitude": self.location.latitude,
                "longitude": self.location.longitude,
                "heading": self.location.heading,
            })

        return attrs

    def is_expired(self, timeout: timedelta = VEHICLE_INACTIVE_TIMEOUT) -> bool:
        """Check if vehicle data is expired."""
        return datetime.now() - self.last_updated > timeout

    def is_trip_completed(self) -> bool:
        """Check if trip is marked as completed."""
        return self.trip_completed_at is not None

    def is_stalled_at_index(self) -> bool:
        """Check if vehicle has been stalled at the same index for too long."""
        if self.first_seen_at_index is None or self.last_seen_index is None:
            return False

        stalled_duration = datetime.now() - self.first_seen_at_index
        return stalled_duration.total_seconds() > VEHICLE_STALLED_AT_INDEX_TIMEOUT

    def should_be_removed(self) -> bool:
        """Determine if vehicle should be removed based on various criteria."""
        now = datetime.now()

        # 1. If trip is explicitly completed, remove after grace period
        if self.trip_completed_at:
            grace_period_elapsed = (now - self.trip_completed_at).total_seconds() > VEHICLE_TRIP_COMPLETED_GRACE_PERIOD
            if grace_period_elapsed:
                _LOGGER.debug("Vehicle %s ready for removal (trip completed + grace period)", self.vehicle_id)
                return True

        # 2. If vehicle is stalled at the same index (likely at end of route)
        if self.is_stalled_at_index():
            _LOGGER.debug("Vehicle %s ready for removal (stalled at index %s)", self.vehicle_id, self.last_seen_index)
            return True

        # 3. Fallback timeout for any vehicle
        time_since_update = (now - self.last_updated).total_seconds()
        if time_since_update > VEHICLE_FALLBACK_TIMEOUT:
            _LOGGER.debug("Vehicle %s ready for removal (fallback timeout)", self.vehicle_id)
            return True

        return False

    def mark_trip_completed(self, reason: str = "manual") -> None:
        """Manually mark trip as completed."""
        if not self.trip_completed_at:
            self.trip_completed_at = datetime.now()
            _LOGGER.info("Vehicle %s trip marked as completed (%s)", self.vehicle_id, reason)

    def update_stop_info(self, current_stop: str | None, next_stop: str | None, stops_remaining: str | int | None) -> None:
        """Update stop information for the vehicle."""
        self.current_stop_name = current_stop
        self.next_stop_name = next_stop
        self.stops_remaining = stops_remaining

        if next_stop:
            _LOGGER.debug("Vehicle %s next stop: %s (%s remaining)",
                         self.vehicle_id, next_stop, stops_remaining or "unknown")

    @classmethod
    def from_websocket_data(cls, data: dict[str, Any]) -> Vehicle | None:
        """Create Vehicle from WebSocket data.

        Args:
            data: WebSocket message data

        Returns:
            Vehicle instance or None if data is invalid
        """
        try:
            vehicle_id = data.get("vehicleId")
            if not vehicle_id:
                _LOGGER.debug("Missing vehicle ID in data")
                return None

            message_type = data.get("type")

            if message_type == "REMOVED":
                # Create inactive vehicle
                return cls(
                    vehicle_id=vehicle_id,
                    trip_id=data.get("tripId"),
                    line_number=data.get("lineNumber"),
                    trip_template_id=data.get("tripTemplateId"),
                    location=None,
                    last_updated=datetime.now(),
                    is_active=False,
                )

            elif message_type == "APPROACHING":
                # Create active vehicle with location
                location_data = data.get("vehicleLocation")
                location = VehicleLocation.from_dict(location_data) if location_data else None

                # Initialize index tracking
                current_index = data.get("index")
                now = datetime.now()

                return cls(
                    vehicle_id=vehicle_id,
                    trip_id=data.get("tripId"),
                    line_number=data.get("lineNumber"),
                    trip_template_id=data.get("tripTemplateId"),
                    location=location,
                    last_updated=now,
                    is_active=True,
                    last_seen_index=current_index,
                    first_seen_at_index=now if current_index is not None else None,
                )

            else:
                _LOGGER.debug("Unknown message type: %s", message_type)
                return None

        except Exception as err:
            _LOGGER.error("Error creating vehicle from data: %s", err)
            return None


class VehicleManager:
    """Manages active vehicles and their data."""

    def __init__(self) -> None:
        """Initialize vehicle manager."""
        self._vehicles: dict[str, Vehicle] = {}
        self._last_cleanup = datetime.now()

    def update_vehicle(self, data: dict[str, Any]) -> Vehicle | None:
        """Update or create vehicle from WebSocket data.

        Args:
            data: WebSocket message data

        Returns:
            Updated Vehicle instance or None
        """
        vehicle_id = data.get("vehicleId")
        if not vehicle_id:
            return None

        # Get or create vehicle
        vehicle = self._vehicles.get(vehicle_id)
        if vehicle is None:
            vehicle = Vehicle.from_websocket_data(data)
            if vehicle:
                self._vehicles[vehicle_id] = vehicle
                _LOGGER.debug("Created new vehicle: %s", vehicle_id)
        else:
            vehicle.update_from_websocket(data)

        # Perform periodic cleanup
        self._maybe_cleanup()

        return vehicle

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None:
        """Get vehicle by ID."""
        return self._vehicles.get(vehicle_id)

    def get_active_vehicles(self) -> list[Vehicle]:
        """Get all active vehicles."""
        return [v for v in self._vehicles.values() if v.is_active]

    def get_vehicles_for_line(self, line_number: str) -> list[Vehicle]:
        """Get all active vehicles for a specific line."""
        return [
            v for v in self._vehicles.values()
            if v.is_active and v.line_number == line_number
        ]

    def get_all_vehicles(self) -> dict[str, Vehicle]:
        """Get all vehicles (active and inactive)."""
        return self._vehicles.copy()

    def remove_vehicle(self, vehicle_id: str) -> bool:
        """Remove vehicle from tracking.

        Args:
            vehicle_id: ID of vehicle to remove

        Returns:
            True if vehicle was removed, False if not found
        """
        if vehicle_id in self._vehicles:
            del self._vehicles[vehicle_id]
            _LOGGER.debug("Removed vehicle %s from tracking", vehicle_id)
            return True
        return False

    def cleanup_expired_vehicles(self) -> int:
        """Remove vehicles that should be cleaned up based on smart completion detection.

        Returns:
            Number of vehicles removed
        """
        vehicles_to_remove = []

        for vehicle_id, vehicle in self._vehicles.items():
            if vehicle.should_be_removed():
                vehicles_to_remove.append(vehicle_id)

        for vehicle_id in vehicles_to_remove:
            vehicle = self._vehicles[vehicle_id]
            reason = "trip completed" if vehicle.is_trip_completed() else "timeout/stalled"
            _LOGGER.info("Removing vehicle %s (reason: %s)", vehicle_id, reason)
            del self._vehicles[vehicle_id]

        if vehicles_to_remove:
            _LOGGER.info("Cleaned up %d vehicles using smart completion detection", len(vehicles_to_remove))

        self._last_cleanup = datetime.now()
        return len(vehicles_to_remove)

    def _maybe_cleanup(self) -> None:
        """Perform cleanup if interval has passed."""
        if datetime.now() - self._last_cleanup > VEHICLE_CLEANUP_INTERVAL:
            self.cleanup_expired_vehicles()

    @property
    def vehicle_count(self) -> int:
        """Return total number of tracked vehicles."""
        return len(self._vehicles)

    @property
    def active_vehicle_count(self) -> int:
        """Return number of active vehicles."""
        return len([v for v in self._vehicles.values() if v.is_active])

    def mark_vehicles_not_in_api_as_completed(self, active_trip_ids: set[str]) -> int:
        """Mark vehicles as trip completed if they no longer appear in API data.

        Args:
            active_trip_ids: Set of trip IDs currently present in API responses

        Returns:
            Number of vehicles marked as completed
        """
        marked_count = 0

        for vehicle in self._vehicles.values():
            # Skip if already marked as completed or no trip_id
            if vehicle.is_trip_completed() or not vehicle.trip_id:
                continue

            # If vehicle's trip is no longer in active API data, mark as completed
            if vehicle.trip_id not in active_trip_ids and vehicle.is_active:
                vehicle.mark_trip_completed("disappeared from API")
                marked_count += 1

        if marked_count > 0:
            _LOGGER.debug("Marked %d vehicles as completed (disappeared from API)", marked_count)

        return marked_count

    def update_vehicles_with_stop_data(self, stops_data: dict[str, Any]) -> int:
        """Update vehicles with next stop information from API data.

        Args:
            stops_data: Dictionary of stop data from coordinator

        Returns:
            Number of vehicles updated with stop information
        """
        updated_count = 0

        # Create a mapping of trip_id -> departure info for quick lookup
        trip_to_departure: dict[str, dict[str, Any]] = {}

        for stop_data in stops_data.values():
            departures = stop_data.get("departures", [])
            for departure in departures:
                trip_id = departure.get("tripId")
                if trip_id:
                    trip_to_departure[trip_id] = departure

        # Update vehicles with stop information
        for vehicle in self._vehicles.values():
            if not vehicle.trip_id or not vehicle.is_active:
                continue

            departure_info = trip_to_departure.get(vehicle.trip_id)
            if departure_info:
                # Extract current stop info from the departure
                current_stop = departure_info.get("stopName")
                destination = departure_info.get("destinationName")

                # Calculate stops remaining based on current index if available
                stops_remaining = None
                if vehicle.last_seen_index is not None:
                    # This is a rough estimate - we don't have total stops in the API
                    # But we can provide relative progress info
                    stops_remaining = f"Index {vehicle.last_seen_index}"

                vehicle.update_stop_info(
                    current_stop=current_stop,
                    next_stop=destination,  # Final destination as "next major stop"
                    stops_remaining=stops_remaining
                )
                updated_count += 1

        if updated_count > 0:
            _LOGGER.debug("Updated %d vehicles with stop information", updated_count)

        return updated_count