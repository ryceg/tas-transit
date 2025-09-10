"""GTFS data manager for Tasmanian Transport integration."""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from .const import (
    GTFS_CACHE_DIR,
    GTFS_DOWNLOAD_TIMEOUT,
    GTFS_HOBART_URL,
    GTFS_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class GTFSData:
    """Container for parsed GTFS data."""

    def __init__(self) -> None:
        """Initialize GTFS data container."""
        self.routes: dict[str, dict[str, Any]] = {}
        self.trips: dict[str, dict[str, Any]] = {}
        self.stops: dict[str, dict[str, Any]] = {}
        self.shapes: dict[str, list[dict[str, Any]]] = {}
        self.stop_times: dict[str, list[dict[str, Any]]] = {}
        self.last_updated: datetime | None = None


class GTFSManager:
    """Manages GTFS data download, parsing, and lookups."""

    def __init__(self, hass_config_dir: str) -> None:
        """Initialize GTFS manager.
        
        Args:
            hass_config_dir: Home Assistant config directory path
        """
        self.hass_config_dir = hass_config_dir
        self.cache_dir = Path(hass_config_dir) / GTFS_CACHE_DIR
        self.gtfs_data = GTFSData()
        self._session: aiohttp.ClientSession | None = None
        self._last_download: datetime | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def initialize(self) -> bool:
        """Initialize GTFS data. Download if needed, then parse.
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Create cache directory if it doesn't exist
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            
            # Check if we need to download fresh data
            if await self._should_refresh_data():
                _LOGGER.info("Downloading fresh GTFS data")
                if not await self._download_gtfs_data():
                    _LOGGER.error("Failed to download GTFS data")
                    return False
            
            # Parse the GTFS data
            if await self._parse_gtfs_data():
                _LOGGER.info("GTFS data initialized successfully")
                return True
            else:
                _LOGGER.error("Failed to parse GTFS data")
                return False
                
        except Exception as err:
            _LOGGER.error("Error initializing GTFS data: %s", err)
            return False

    async def _should_refresh_data(self) -> bool:
        """Check if GTFS data should be refreshed."""
        gtfs_zip_path = self.cache_dir / "google_transit.zip"
        
        # Download if file doesn't exist
        if not gtfs_zip_path.exists():
            return True
        
        # Download if file is older than refresh interval
        file_age = datetime.now() - datetime.fromtimestamp(gtfs_zip_path.stat().st_mtime)
        if file_age.total_seconds() > GTFS_REFRESH_INTERVAL:
            return True
        
        return False

    async def _download_gtfs_data(self) -> bool:
        """Download GTFS ZIP file from MetroTas.
        
        Returns:
            True if download successful, False otherwise
        """
        try:
            session = await self._get_session()
            gtfs_zip_path = self.cache_dir / "google_transit.zip"
            
            _LOGGER.info("Downloading GTFS data from %s", GTFS_HOBART_URL)
            
            async with asyncio.timeout(GTFS_DOWNLOAD_TIMEOUT):
                async with session.get(GTFS_HOBART_URL) as response:
                    response.raise_for_status()
                    
                    # Write to temporary file first
                    temp_path = gtfs_zip_path.with_suffix(".tmp")
                    with open(temp_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            f.write(chunk)
                    
                    # Move to final location
                    temp_path.rename(gtfs_zip_path)
                    
            self._last_download = datetime.now()
            _LOGGER.info("GTFS data downloaded successfully")
            return True
            
        except Exception as err:
            _LOGGER.error("Error downloading GTFS data: %s", err)
            return False

    async def _parse_gtfs_data(self) -> bool:
        """Parse GTFS ZIP file and load data into memory.
        
        Returns:
            True if parsing successful, False otherwise
        """
        try:
            gtfs_zip_path = self.cache_dir / "google_transit.zip"
            
            if not gtfs_zip_path.exists():
                _LOGGER.error("GTFS ZIP file not found: %s", gtfs_zip_path)
                return False
            
            _LOGGER.info("Parsing GTFS data from %s", gtfs_zip_path)
            
            with zipfile.ZipFile(gtfs_zip_path, 'r') as zip_file:
                # Parse routes.txt
                if await self._parse_routes(zip_file):
                    _LOGGER.debug("Parsed routes.txt successfully")
                
                # Parse trips.txt  
                if await self._parse_trips(zip_file):
                    _LOGGER.debug("Parsed trips.txt successfully")
                
                # Parse stops.txt
                if await self._parse_stops(zip_file):
                    _LOGGER.debug("Parsed stops.txt successfully")
                
                # Parse shapes.txt
                if await self._parse_shapes(zip_file):
                    _LOGGER.debug("Parsed shapes.txt successfully")
                
                # Parse stop_times.txt (optional, for future use)
                if await self._parse_stop_times(zip_file):
                    _LOGGER.debug("Parsed stop_times.txt successfully")
            
            self.gtfs_data.last_updated = datetime.now()
            _LOGGER.info("GTFS parsing completed. Loaded %d routes, %d trips, %d stops", 
                        len(self.gtfs_data.routes), len(self.gtfs_data.trips), len(self.gtfs_data.stops))
            return True
            
        except Exception as err:
            _LOGGER.error("Error parsing GTFS data: %s", err)
            return False

    async def _parse_routes(self, zip_file: zipfile.ZipFile) -> bool:
        """Parse routes.txt from GTFS ZIP."""
        try:
            if "routes.txt" not in zip_file.namelist():
                _LOGGER.warning("routes.txt not found in GTFS ZIP")
                return False
            
            with zip_file.open("routes.txt") as f:
                content = f.read().decode("utf-8")
                reader = csv.DictReader(content.splitlines())
                
                for row in reader:
                    route_id = row.get("route_id")
                    if route_id:
                        self.gtfs_data.routes[route_id] = {
                            "route_short_name": row.get("route_short_name", ""),
                            "route_long_name": row.get("route_long_name", ""),
                            "route_color": row.get("route_color", ""),
                            "route_text_color": row.get("route_text_color", ""),
                            "route_type": row.get("route_type", ""),
                        }
            return True
            
        except Exception as err:
            _LOGGER.error("Error parsing routes.txt: %s", err)
            return False

    async def _parse_trips(self, zip_file: zipfile.ZipFile) -> bool:
        """Parse trips.txt from GTFS ZIP."""
        try:
            if "trips.txt" not in zip_file.namelist():
                _LOGGER.warning("trips.txt not found in GTFS ZIP")
                return False
            
            with zip_file.open("trips.txt") as f:
                content = f.read().decode("utf-8")
                reader = csv.DictReader(content.splitlines())
                
                for row in reader:
                    trip_id = row.get("trip_id")
                    if trip_id:
                        # Parse wheelchair accessibility
                        wheelchair_accessible = int(row.get("wheelchair_accessible", "0"))
                        
                        self.gtfs_data.trips[trip_id] = {
                            "route_id": row.get("route_id", ""),
                            "service_id": row.get("service_id", ""),
                            "trip_headsign": row.get("trip_headsign", ""),
                            "direction_id": row.get("direction_id", ""),
                            "shape_id": row.get("shape_id", ""),
                            "wheelchair_accessible": wheelchair_accessible,
                        }
            return True
            
        except Exception as err:
            _LOGGER.error("Error parsing trips.txt: %s", err)
            return False

    async def _parse_stops(self, zip_file: zipfile.ZipFile) -> bool:
        """Parse stops.txt from GTFS ZIP."""
        try:
            if "stops.txt" not in zip_file.namelist():
                _LOGGER.warning("stops.txt not found in GTFS ZIP")
                return False
            
            with zip_file.open("stops.txt") as f:
                content = f.read().decode("utf-8")
                reader = csv.DictReader(content.splitlines())
                
                for row in reader:
                    stop_id = row.get("stop_id")
                    if stop_id:
                        self.gtfs_data.stops[stop_id] = {
                            "stop_name": row.get("stop_name", ""),
                            "stop_lat": float(row.get("stop_lat", "0")) if row.get("stop_lat") else 0.0,
                            "stop_lon": float(row.get("stop_lon", "0")) if row.get("stop_lon") else 0.0,
                            "zone_id": row.get("zone_id", ""),
                            "stop_code": row.get("stop_code", ""),
                        }
            return True
            
        except Exception as err:
            _LOGGER.error("Error parsing stops.txt: %s", err)
            return False

    async def _parse_shapes(self, zip_file: zipfile.ZipFile) -> bool:
        """Parse shapes.txt from GTFS ZIP."""
        try:
            if "shapes.txt" not in zip_file.namelist():
                _LOGGER.warning("shapes.txt not found in GTFS ZIP")
                return False
            
            with zip_file.open("shapes.txt") as f:
                content = f.read().decode("utf-8")
                reader = csv.DictReader(content.splitlines())
                
                for row in reader:
                    shape_id = row.get("shape_id")
                    if shape_id:
                        if shape_id not in self.gtfs_data.shapes:
                            self.gtfs_data.shapes[shape_id] = []
                        
                        try:
                            shape_point = {
                                "shape_pt_lat": float(row.get("shape_pt_lat", "0")),
                                "shape_pt_lon": float(row.get("shape_pt_lon", "0")),
                                "shape_pt_sequence": int(row.get("shape_pt_sequence", "0")),
                            }
                            
                            # shape_dist_traveled is optional
                            dist_traveled = row.get("shape_dist_traveled")
                            if dist_traveled and dist_traveled.strip():
                                try:
                                    shape_point["shape_dist_traveled"] = float(dist_traveled)
                                except (ValueError, TypeError):
                                    shape_point["shape_dist_traveled"] = None
                            else:
                                shape_point["shape_dist_traveled"] = None
                            
                            self.gtfs_data.shapes[shape_id].append(shape_point)
                            
                        except (ValueError, TypeError) as err:
                            _LOGGER.debug("Skipping invalid shape point for %s: %s", shape_id, err)
                            continue
            
            # Sort shape points by sequence
            for shape_id in self.gtfs_data.shapes:
                self.gtfs_data.shapes[shape_id].sort(key=lambda x: x["shape_pt_sequence"])
            
            return True
            
        except Exception as err:
            _LOGGER.error("Error parsing shapes.txt: %s", err)
            return False

    async def _parse_stop_times(self, zip_file: zipfile.ZipFile) -> bool:
        """Parse stop_times.txt from GTFS ZIP (for future use)."""
        try:
            if "stop_times.txt" not in zip_file.namelist():
                _LOGGER.warning("stop_times.txt not found in GTFS ZIP")
                return False
            
            # Note: stop_times.txt can be very large, so we might want to
            # implement selective parsing or indexing in the future
            _LOGGER.debug("stop_times.txt parsing skipped for now (large file)")
            return True
            
        except Exception as err:
            _LOGGER.error("Error parsing stop_times.txt: %s", err)
            return False

    def get_trip_info(self, trip_id: str) -> dict[str, Any] | None:
        """Get GTFS trip information by trip ID.
        
        Args:
            trip_id: GTFS trip ID
            
        Returns:
            Trip information dict or None if not found
        """
        return self.gtfs_data.trips.get(trip_id)

    def get_route_info(self, route_id: str) -> dict[str, Any] | None:
        """Get GTFS route information by route ID.
        
        Args:
            route_id: GTFS route ID
            
        Returns:
            Route information dict or None if not found
        """
        return self.gtfs_data.routes.get(route_id)

    def get_stop_info(self, stop_id: str) -> dict[str, Any] | None:
        """Get GTFS stop information by stop ID.
        
        Args:
            stop_id: GTFS stop ID
            
        Returns:
            Stop information dict or None if not found
        """
        return self.gtfs_data.stops.get(stop_id)

    def get_shape_points(self, shape_id: str) -> list[dict[str, Any]] | None:
        """Get route shape points by shape ID.
        
        Args:
            shape_id: GTFS shape ID
            
        Returns:
            List of shape points or None if not found
        """
        return self.gtfs_data.shapes.get(shape_id)

    def enrich_departure_data(self, departure: dict[str, Any]) -> dict[str, Any]:
        """Enrich real-time departure data with GTFS information.
        
        Args:
            departure: Real-time departure data
            
        Returns:
            Enriched departure data with GTFS fields
        """
        enriched = departure.copy()
        
        trip_id = departure.get("tripId")
        if not trip_id:
            return enriched
        
        # Get trip information
        trip_info = self.get_trip_info(trip_id)
        if trip_info:
            # Add wheelchair accessibility
            wheelchair_accessible = trip_info.get("wheelchair_accessible", 0)
            enriched["wheelchair_accessible"] = wheelchair_accessible
            enriched["wheelchair_accessible_text"] = self._get_wheelchair_text(wheelchair_accessible)
            
            # Add trip headsign if available
            trip_headsign = trip_info.get("trip_headsign")
            if trip_headsign:
                enriched["trip_headsign"] = trip_headsign
            
            # Add route information
            route_id = trip_info.get("route_id")
            if route_id:
                route_info = self.get_route_info(route_id)
                if route_info:
                    enriched["route_color"] = route_info.get("route_color", "")
                    enriched["route_text_color"] = route_info.get("route_text_color", "")
                    enriched["route_long_name"] = route_info.get("route_long_name", "")
            
            # Add shape ID for route visualization
            shape_id = trip_info.get("shape_id")
            if shape_id:
                enriched["shape_id"] = shape_id
        
        return enriched

    def _get_wheelchair_text(self, wheelchair_accessible: int) -> str:
        """Convert wheelchair accessibility code to text.
        
        Args:
            wheelchair_accessible: GTFS wheelchair_accessible value
            
        Returns:
            Human-readable wheelchair accessibility status
        """
        wheelchair_map = {
            0: "No information",
            1: "Wheelchair accessible",
            2: "Not wheelchair accessible",
        }
        return wheelchair_map.get(wheelchair_accessible, "Unknown")

    @property
    def is_data_available(self) -> bool:
        """Check if GTFS data is available and loaded."""
        return (
            self.gtfs_data.last_updated is not None
            and len(self.gtfs_data.trips) > 0
        )

    @property
    def data_age(self) -> timedelta | None:
        """Get age of loaded GTFS data."""
        if self.gtfs_data.last_updated:
            return datetime.now() - self.gtfs_data.last_updated
        return None