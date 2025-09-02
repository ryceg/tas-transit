"""Vehicle data models and management for Tasmanian Transport integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Vehicle cleanup settings
VEHICLE_INACTIVE_TIMEOUT = timedelta(minutes=10)  # Remove vehicles after 10 minutes of no updates
VEHICLE_CLEANUP_INTERVAL = timedelta(minutes=5)   # Run cleanup every 5 minutes


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
            return cls(
                latitude=float(location.get("latitude", 0)),
                longitude=float(location.get("longitude", 0)),
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
                
                _LOGGER.debug("Updated vehicle %s: route %s, location %s", 
                            self.vehicle_id, self.line_number, 
                            f"{self.location.latitude:.4f},{self.location.longitude:.4f}" if self.location else "unknown")
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
                
                return cls(
                    vehicle_id=vehicle_id,
                    trip_id=data.get("tripId"),
                    line_number=data.get("lineNumber"),
                    trip_template_id=data.get("tripTemplateId"),
                    location=location,
                    last_updated=datetime.now(),
                    is_active=True,
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
        """Remove expired vehicles from tracking.
        
        Returns:
            Number of vehicles removed
        """
        expired_vehicles = [
            vehicle_id for vehicle_id, vehicle in self._vehicles.items()
            if vehicle.is_expired()
        ]
        
        for vehicle_id in expired_vehicles:
            del self._vehicles[vehicle_id]
            
        if expired_vehicles:
            _LOGGER.info("Cleaned up %d expired vehicles", len(expired_vehicles))
            
        self._last_cleanup = datetime.now()
        return len(expired_vehicles)

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