"""Route shape handling for Tasmanian Transport integration."""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class ShapeManager:
    """Manages route shape data from the shapes API endpoint."""

    def __init__(self, api) -> None:
        """Initialize shape manager.

        Args:
            api: TasTransitApi instance
        """
        self.api = api
        self._shapes_cache: dict[str, Any] | None = None
        self._stop_connections: dict[str, list[dict[str, Any]]] = {}

    async def initialize(self) -> bool:
        """Initialize shape data from the API.

        Returns:
            True if initialization successful, False otherwise
        """
        try:
            _LOGGER.info("Initializing route shapes data")
            shapes_data = await self.api.get_shapes()

            if not shapes_data or "links" not in shapes_data:
                _LOGGER.warning("No shapes data received from API")
                return False

            self._shapes_cache = shapes_data
            self._build_stop_connections()

            _LOGGER.info("Route shapes data initialized with %d route links",
                        len(shapes_data.get("links", [])))
            return True

        except Exception as err:
            _LOGGER.error("Error initializing route shapes data: %s", err)
            return False

    def _build_stop_connections(self) -> None:
        """Build a lookup of connections between stops."""
        if not self._shapes_cache:
            return

        self._stop_connections.clear()

        for link in self._shapes_cache.get("links", []):
            start_stop = link.get("startStop", {})
            start_stop_id = start_stop.get("id")

            if start_stop_id:
                if start_stop_id not in self._stop_connections:
                    self._stop_connections[start_stop_id] = []

                self._stop_connections[start_stop_id].append(link)

        _LOGGER.debug("Built stop connections for %d stops", len(self._stop_connections))

    @property
    def is_data_available(self) -> bool:
        """Check if shapes data is available."""
        return self._shapes_cache is not None

    def get_route_coordinates_for_stops(self, start_stop_id: str, end_stop_id: str) -> list[list[float]] | None:
        """Get route coordinates between two stops.

        Args:
            start_stop_id: Starting stop ID
            end_stop_id: Ending stop ID

        Returns:
            List of [longitude, latitude] coordinate pairs, or None if not found
        """
        if not self.is_data_available:
            return None

        # Find the link that connects these stops
        connections = self._stop_connections.get(start_stop_id, [])

        for link in connections:
            end_stop = link.get("endStop", {})
            if end_stop.get("id") == end_stop_id:
                route_points = link.get("routePoints", [])
                if route_points:
                    # Convert to [longitude, latitude] format for GeoJSON/mapping
                    coordinates = []
                    for point in route_points:
                        lat = point.get("latitude")
                        lon = point.get("longitude")
                        if lat is not None and lon is not None:
                            coordinates.append([lon, lat])  # [longitude, latitude] format

                    return coordinates if coordinates else None

        return None

    def get_route_geojson_for_stops(self, start_stop_id: str, end_stop_id: str) -> dict[str, Any] | None:
        """Get route as GeoJSON LineString between two stops.

        Args:
            start_stop_id: Starting stop ID
            end_stop_id: Ending stop ID

        Returns:
            GeoJSON LineString feature or None if not found
        """
        coordinates = self.get_route_coordinates_for_stops(start_stop_id, end_stop_id)
        if not coordinates:
            return None

        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates
            },
            "properties": {
                "start_stop_id": start_stop_id,
                "end_stop_id": end_stop_id
            }
        }

    def get_all_route_segments(self) -> list[dict[str, Any]]:
        """Get all route segments as GeoJSON features.

        Returns:
            List of GeoJSON LineString features for all route segments
        """
        if not self.is_data_available:
            return []

        segments = []

        for link in self._shapes_cache.get("links", []):
            start_stop = link.get("startStop", {})
            end_stop = link.get("endStop", {})
            route_points = link.get("routePoints", [])

            start_stop_id = start_stop.get("id")
            end_stop_id = end_stop.get("id")

            if start_stop_id and end_stop_id and route_points:
                coordinates = []
                for point in route_points:
                    lat = point.get("latitude")
                    lon = point.get("longitude")
                    if lat is not None and lon is not None:
                        coordinates.append([lon, lat])  # [longitude, latitude] format

                if coordinates:
                    segments.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": coordinates
                        },
                        "properties": {
                            "start_stop_id": start_stop_id,
                            "start_stop_name": start_stop.get("name", ""),
                            "end_stop_id": end_stop_id,
                            "end_stop_name": end_stop.get("name", ""),
                            "start_stop_code": start_stop.get("code", ""),
                            "end_stop_code": end_stop.get("code", ""),
                        }
                    })

        return segments

    def get_stops_info(self) -> dict[str, dict[str, Any]]:
        """Get information about all stops from the shapes data.

        Returns:
            Dict mapping stop_id to stop information
        """
        if not self.is_data_available:
            return {}

        stops_info = {}

        for link in self._shapes_cache.get("links", []):
            # Process start stop
            start_stop = link.get("startStop", {})
            start_stop_id = start_stop.get("id")
            if start_stop_id:
                stops_info[start_stop_id] = {
                    "id": start_stop_id,
                    "name": start_stop.get("name", ""),
                    "code": start_stop.get("code", ""),
                    "location": start_stop.get("location", {}),
                    "zone": start_stop.get("zone", ""),
                    "platformCode": start_stop.get("platformCode"),
                    "parentStation": start_stop.get("parentStation", ""),
                    "station": start_stop.get("station", False),
                }

            # Process end stop
            end_stop = link.get("endStop", {})
            end_stop_id = end_stop.get("id")
            if end_stop_id:
                stops_info[end_stop_id] = {
                    "id": end_stop_id,
                    "name": end_stop.get("name", ""),
                    "code": end_stop.get("code", ""),
                    "location": end_stop.get("location", {}),
                    "zone": end_stop.get("zone", ""),
                    "platformCode": end_stop.get("platformCode"),
                    "parentStation": end_stop.get("parentStation", ""),
                    "station": end_stop.get("station", False),
                }

        return stops_info

    def get_connected_stops(self, stop_id: str) -> list[dict[str, Any]]:
        """Get all stops connected to a given stop.

        Args:
            stop_id: Stop ID to find connections for

        Returns:
            List of connected stop information
        """
        if not self.is_data_available:
            return []

        connected_stops = []
        connections = self._stop_connections.get(stop_id, [])

        for link in connections:
            end_stop = link.get("endStop", {})
            if end_stop:
                connected_stops.append({
                    "stop_id": end_stop.get("id"),
                    "stop_name": end_stop.get("name", ""),
                    "stop_code": end_stop.get("code", ""),
                    "location": end_stop.get("location", {}),
                    "route_points_count": len(link.get("routePoints", [])),
                })

        return connected_stops

    def get_route_bounds_for_stops(self, start_stop_id: str, end_stop_id: str) -> dict[str, float] | None:
        """Get bounding box for a route segment.

        Args:
            start_stop_id: Starting stop ID
            end_stop_id: Ending stop ID

        Returns:
            Dict with min_lat, max_lat, min_lon, max_lon or None if not found
        """
        coordinates = self.get_route_coordinates_for_stops(start_stop_id, end_stop_id)
        if not coordinates:
            return None

        lons = [coord[0] for coord in coordinates]
        lats = [coord[1] for coord in coordinates]

        return {
            "min_lat": min(lats),
            "max_lat": max(lats),
            "min_lon": min(lons),
            "max_lon": max(lons),
        }

    def get_simplified_coordinates_for_stops(self, start_stop_id: str, end_stop_id: str, max_points: int = 50) -> list[list[float]] | None:
        """Get simplified route coordinates for performance.

        Args:
            start_stop_id: Starting stop ID
            end_stop_id: Ending stop ID
            max_points: Maximum number of points to return

        Returns:
            Simplified list of [longitude, latitude] coordinate pairs
        """
        coordinates = self.get_route_coordinates_for_stops(start_stop_id, end_stop_id)
        if not coordinates:
            return None

        # If the route has too many points, simplify by taking every nth point
        if len(coordinates) <= max_points:
            return coordinates

        step = len(coordinates) // max_points
        simplified = []

        # Always include the first point
        simplified.append(coordinates[0])

        # Take every nth point
        for i in range(step, len(coordinates), step):
            simplified.append(coordinates[i])

        # Always include the last point if it's not already included
        if coordinates[-1] not in simplified:
            simplified.append(coordinates[-1])

        _LOGGER.debug("Simplified route from %d to %d points", len(coordinates), len(simplified))
        return simplified