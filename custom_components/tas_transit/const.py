"""Constants for the Tasmanian Transport integration."""
from typing import Final

DOMAIN: Final = "tas_transit"

# API Configuration
API_BASE_URL: Final = "https://real-time.transport.tas.gov.au/timetable/rest"
API_STOPS_SEARCH: Final = f"{API_BASE_URL}/stops/searchbylocation"
API_STOPDISPLAYS: Final = f"{API_BASE_URL}/stopdisplays"
API_STOPSCHEDULE: Final = f"{API_BASE_URL}/currentstopschedule"
API_SHAPES: Final = f"{API_BASE_URL}/shapes"
API_TIMEOUT: Final = 30

# Update Intervals
UPDATE_INTERVAL_DEFAULT: Final = 60   # 1 minute - default update interval
UPDATE_INTERVAL_FREQUENT: Final = 20  # 20 seconds - when bus within 30 minutes
UPDATE_INTERVAL_THRESHOLD: Final = 30 # 30 minutes - switch to frequent updates

# Configuration Keys
CONF_STOPS: Final = "stops"
CONF_STOP_ID: Final = "stop_id"
CONF_STOP_NAME: Final = "stop_name"
CONF_LINE_FILTERS: Final = "line_filters"
CONF_DESTINATION_FILTERS: Final = "destination_filters"
CONF_FILTER_MODE: Final = "filter_mode"

# Sensor Names
SENSOR_NEXT_BUS: Final = "next_bus_departure"
SENSOR_TIME_TO_DEPARTURE: Final = "time_to_departure"
SENSOR_BUS_ROUTE: Final = "bus_route"

# Web URLs
TRANSPORT_WEB_URL: Final = "https://real-time.transport.tas.gov.au/timetable/#?stop="

# WebSocket Configuration
WEBSOCKET_URL: Final = "wss://real-time.transport.tas.gov.au/timetable/websocket/all?map"
WEBSOCKET_TIMEOUT: Final = 30
WEBSOCKET_HEARTBEAT: Final = 60

# Vehicle Tracking
VEHICLE_INACTIVE_TIMEOUT: Final = 600  # 10 minutes in seconds - fallback timeout
VEHICLE_CLEANUP_INTERVAL: Final = 300  # 5 minutes in seconds

# Trip Completion Detection
VEHICLE_TRIP_COMPLETED_GRACE_PERIOD: Final = 300    # 5 minutes after trip completion detected
VEHICLE_STALLED_AT_INDEX_TIMEOUT: Final = 900       # 15 minutes if stalled at same index
VEHICLE_FALLBACK_TIMEOUT: Final = 3600              # 60 minutes absolute fallback timeout

# Filter Configuration
FILTER_MODE_INCLUDE: Final = "include"
FILTER_MODE_EXCLUDE: Final = "exclude"

