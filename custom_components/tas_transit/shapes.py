"""Route shape handling for Tasmanian Transport integration."""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class RouteShapeManager:
    """Manages route shape data for map visualization."""

    def __init__(self, gtfs_manager) -> None:
        """Initialize route shape manager.
        
        Args:
            gtfs_manager: GTFSManager instance
        """
        self.gtfs_manager = gtfs_manager

    def get_route_coordinates(self, shape_id: str) -> list[list[float]] | None:
        """Get route coordinates for map visualization.
        
        Args:
            shape_id: GTFS shape ID
            
        Returns:
            List of [longitude, latitude] coordinate pairs, or None if not found
        """
        if not self.gtfs_manager.is_data_available:
            return None
        
        shape_points = self.gtfs_manager.get_shape_points(shape_id)
        if not shape_points:
            return None
        
        # Convert to [longitude, latitude] format for GeoJSON/mapping
        coordinates = []
        for point in shape_points:
            lat = point.get("shape_pt_lat")
            lon = point.get("shape_pt_lon")
            if lat is not None and lon is not None:
                coordinates.append([lon, lat])  # [longitude, latitude] format
        
        return coordinates if coordinates else None

    def get_route_geojson(self, shape_id: str) -> dict[str, Any] | None:
        """Get route as GeoJSON LineString for map visualization.
        
        Args:
            shape_id: GTFS shape ID
            
        Returns:
            GeoJSON LineString feature or None if not found
        """
        coordinates = self.get_route_coordinates(shape_id)
        if not coordinates:
            return None
        
        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates
            },
            "properties": {
                "shape_id": shape_id
            }
        }

    def get_simplified_coordinates(self, shape_id: str, max_points: int = 100) -> list[list[float]] | None:
        """Get simplified route coordinates for performance.
        
        Args:
            shape_id: GTFS shape ID
            max_points: Maximum number of points to return
            
        Returns:
            Simplified list of [longitude, latitude] coordinate pairs
        """
        coordinates = self.get_route_coordinates(shape_id)
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

    def get_route_bounds(self, shape_id: str) -> dict[str, float] | None:
        """Get bounding box for a route.
        
        Args:
            shape_id: GTFS shape ID
            
        Returns:
            Dict with min_lat, max_lat, min_lon, max_lon or None if not found
        """
        coordinates = self.get_route_coordinates(shape_id)
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

    def get_active_route_shapes(self, trip_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Get route shapes for active trips.
        
        Args:
            trip_ids: List of active trip IDs
            
        Returns:
            Dict mapping shape_id to route visualization data
        """
        if not self.gtfs_manager.is_data_available:
            return {}
        
        route_shapes = {}
        
        for trip_id in trip_ids:
            trip_info = self.gtfs_manager.get_trip_info(trip_id)
            if not trip_info:
                continue
            
            shape_id = trip_info.get("shape_id")
            if not shape_id or shape_id in route_shapes:
                continue
            
            # Get route information for styling
            route_id = trip_info.get("route_id")
            route_info = self.gtfs_manager.get_route_info(route_id) if route_id else None
            
            # Get simplified coordinates for performance
            coordinates = self.get_simplified_coordinates(shape_id)
            if not coordinates:
                continue
            
            route_shapes[shape_id] = {
                "coordinates": coordinates,
                "trip_id": trip_id,
                "route_id": route_id,
                "route_short_name": route_info.get("route_short_name", "") if route_info else "",
                "route_color": route_info.get("route_color", "") if route_info else "",
                "bounds": self.get_route_bounds(shape_id),
            }
        
        return route_shapes

    def get_route_style(self, route_info: dict[str, Any] | None) -> dict[str, Any]:
        """Get map styling for a route.
        
        Args:
            route_info: GTFS route information
            
        Returns:
            Dict with styling properties for map display
        """
        default_style = {
            "color": "#0078D4",  # Default blue
            "weight": 4,
            "opacity": 0.8,
        }
        
        if not route_info:
            return default_style
        
        route_color = route_info.get("route_color", "")
        if route_color and len(route_color) == 6:  # Valid hex color without #
            default_style["color"] = f"#{route_color}"
        
        return default_style