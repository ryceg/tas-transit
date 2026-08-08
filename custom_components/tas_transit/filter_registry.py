"""Filter registry for managing route and destination filters."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class RouteInfo:
    """Information about a route."""

    route_number: str
    first_seen: datetime
    last_seen: datetime
    is_currently_active: bool = True


@dataclass
class DestinationInfo:
    """Information about a destination."""

    destination_name: str
    first_seen: datetime
    last_seen: datetime
    is_currently_active: bool = True


class RouteDestinationRegistry:
    """Registry for tracking routes and destinations."""

    def __init__(self) -> None:
        """Initialize the registry."""
        self._routes: dict[str, RouteInfo] = {}
        self._destinations: dict[str, DestinationInfo] = {}

    def add_route(self, route_number: str) -> bool:
        """Add or update a route in the registry.

        Args:
            route_number: The route number to add

        Returns:
            True if this is a new route, False if it already existed
        """
        if not route_number or not route_number.strip():
            return False

        route_number = route_number.strip()
        now = datetime.now()

        if route_number in self._routes:
            # Update existing route
            self._routes[route_number].last_seen = now
            self._routes[route_number].is_currently_active = True
            return False
        else:
            # Add new route
            self._routes[route_number] = RouteInfo(
                route_number=route_number,
                first_seen=now,
                last_seen=now,
                is_currently_active=True,
            )
            _LOGGER.debug("Discovered new route: %s", route_number)
            return True

    def add_destination(self, destination_name: str) -> bool:
        """Add or update a destination in the registry.

        Args:
            destination_name: The destination name to add

        Returns:
            True if this is a new destination, False if it already existed
        """
        if not destination_name or not destination_name.strip():
            return False

        destination_name = destination_name.strip()
        now = datetime.now()

        if destination_name in self._destinations:
            # Update existing destination
            self._destinations[destination_name].last_seen = now
            self._destinations[destination_name].is_currently_active = True
            return False
        else:
            # Add new destination
            self._destinations[destination_name] = DestinationInfo(
                destination_name=destination_name,
                first_seen=now,
                last_seen=now,
                is_currently_active=True,
            )
            _LOGGER.debug("Discovered new destination: %s", destination_name)
            return True

    def update_active_status(
        self, active_routes: set[str], active_destinations: set[str]
    ) -> None:
        """Update which routes and destinations are currently active.

        Args:
            active_routes: Set of currently active route numbers
            active_destinations: Set of currently active destination names
        """
        # Update route activity
        for route_number, route_info in self._routes.items():
            route_info.is_currently_active = route_number in active_routes

        # Update destination activity
        for dest_name, dest_info in self._destinations.items():
            dest_info.is_currently_active = dest_name in active_destinations

    def get_all_routes(self) -> dict[str, RouteInfo]:
        """Get all tracked routes.

        Returns:
            Dictionary mapping route numbers to RouteInfo objects
        """
        return self._routes.copy()

    def get_all_destinations(self) -> dict[str, DestinationInfo]:
        """Get all tracked destinations.

        Returns:
            Dictionary mapping destination names to DestinationInfo objects
        """
        return self._destinations.copy()

    def get_route(self, route_number: str) -> RouteInfo | None:
        """Get information about a specific route.

        Args:
            route_number: The route number to look up

        Returns:
            RouteInfo if found, None otherwise
        """
        return self._routes.get(route_number)

    def get_destination(self, destination_name: str) -> DestinationInfo | None:
        """Get information about a specific destination.

        Args:
            destination_name: The destination name to look up

        Returns:
            DestinationInfo if found, None otherwise
        """
        return self._destinations.get(destination_name)

    def get_active_routes(self) -> list[str]:
        """Get list of currently active route numbers.

        Returns:
            List of route numbers that are currently active
        """
        return [
            route_number
            for route_number, info in self._routes.items()
            if info.is_currently_active
        ]

    def get_active_destinations(self) -> list[str]:
        """Get list of currently active destination names.

        Returns:
            List of destination names that are currently active
        """
        return [
            dest_name
            for dest_name, info in self._destinations.items()
            if info.is_currently_active
        ]

    @property
    def route_count(self) -> int:
        """Return total number of tracked routes."""
        return len(self._routes)

    @property
    def destination_count(self) -> int:
        """Return total number of tracked destinations."""
        return len(self._destinations)

    @property
    def active_route_count(self) -> int:
        """Return number of currently active routes."""
        return len([r for r in self._routes.values() if r.is_currently_active])

    @property
    def active_destination_count(self) -> int:
        """Return number of currently active destinations."""
        return len([d for d in self._destinations.values() if d.is_currently_active])
