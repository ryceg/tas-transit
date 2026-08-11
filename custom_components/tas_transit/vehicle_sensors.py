"""Vehicle sensor platform for Tasmanian Transport integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TasTransitDataUpdateCoordinator
from .vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)

# Mirror of device_tracker staleness — hide sensors for vehicles we've lost.
STALE_VEHICLE_THRESHOLD = timedelta(seconds=180)


class TasTransitVehicleSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for vehicle sensors."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        vehicle_id: str,
        sensor_type: str,
    ) -> None:
        """Initialize the vehicle sensor."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self.vehicle_id = vehicle_id
        self.sensor_type = sensor_type
        self._attr_unique_id = f"{config_entry.entry_id}_vehicle_{vehicle_id}_{sensor_type}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"vehicle_{vehicle_id}")},
            "name": f"Bus {vehicle_id}",
            "manufacturer": "Tasmanian Government",
            "model": "Transit Vehicle",
        }

    @property
    def vehicle(self) -> Vehicle | None:
        """Get the vehicle data from coordinator."""
        if not hasattr(self.coordinator, 'vehicle_manager'):
            return None
        return self.coordinator.vehicle_manager.get_vehicle(self.vehicle_id)

    @property
    def available(self) -> bool:
        """Return if the vehicle sensor is available."""
        vehicle = self.vehicle
        return (
            vehicle is not None
            and vehicle.is_active
            and datetime.now() - vehicle.last_updated < STALE_VEHICLE_THRESHOLD
        )


class TasTransitVehicleLineNumberSensor(TasTransitVehicleSensorBase):
    """Sensor for vehicle line number."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        vehicle_id: str,
    ) -> None:
        """Initialize the line number sensor."""
        super().__init__(coordinator, config_entry, vehicle_id, "line_number")
        self._attr_name = f"Bus {vehicle_id} Line Number"
        self._attr_icon = "mdi:routes"

    @property
    def native_value(self) -> str | None:
        """Return the vehicle line number."""
        vehicle = self.vehicle
        if not vehicle:
            return None
        return vehicle.line_number

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        vehicle = self.vehicle
        if not vehicle:
            return {}

        attrs = {
            "vehicle_id": self.vehicle_id,
            "trip_id": vehicle.trip_id,
            "trip_template_id": vehicle.trip_template_id,
            "is_active": vehicle.is_active,
            "last_updated": vehicle.last_updated.isoformat(),
        }

        if vehicle.location:
            attrs.update({
                "heading": vehicle.location.heading,
            })
            # Add bearing/direction text if heading is available
            if vehicle.location.heading is not None:
                attrs["direction"] = self._heading_to_direction(vehicle.location.heading)

        return attrs

    def _heading_to_direction(self, heading: float) -> str:
        """Convert heading degrees to cardinal direction."""
        directions = [
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
        ]
        index = round(heading / 22.5) % 16
        return directions[index]


class TasTransitVehicleRealtimeStatusSensor(TasTransitVehicleSensorBase):
    """Sensor for vehicle real-time connection status."""

    def __init__(
        self,
        coordinator: TasTransitDataUpdateCoordinator,
        config_entry: ConfigEntry,
        vehicle_id: str,
    ) -> None:
        """Initialize the real-time status sensor."""
        super().__init__(coordinator, config_entry, vehicle_id, "realtime_status")
        self._attr_name = f"Bus {vehicle_id} Real-time Status"
        self._attr_icon = "mdi:wifi"

    @property
    def native_value(self) -> str:
        """Return the real-time connection status."""
        vehicle = self.vehicle
        if not vehicle:
            return "disconnected"

        if not vehicle.is_active:
            return "inactive"

        # Check if location data is recent (within last 5 minutes)
        now = datetime.now()
        if (now - vehicle.last_updated).total_seconds() < 300:
            return "connected"
        else:
            return "stale"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        vehicle = self.vehicle
        if not vehicle:
            return {"status_description": "Vehicle not found"}

        status = self.native_value
        status_descriptions = {
            "connected": "Receiving live GPS updates",
            "stale": "GPS data is outdated",
            "inactive": "Vehicle is not in service",
            "disconnected": "No connection to vehicle",
        }

        attrs = {
            "vehicle_id": self.vehicle_id,
            "status_description": status_descriptions.get(status, "Unknown status"),
            "last_updated": vehicle.last_updated.isoformat(),
            "is_active": vehicle.is_active,
        }

        if vehicle.location:
            attrs["has_location"] = True
        else:
            attrs["has_location"] = False

        return attrs