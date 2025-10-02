"""Config flow for Tasmanian Transport integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .api import TasTransitApi
from .const import (
    CONF_DESTINATION_FILTERS,
    CONF_FILTER_MODE,
    CONF_LINE_FILTERS,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    FILTER_MODE_INCLUDE,
    FILTER_MODE_EXCLUDE,
    TRANSPORT_WEB_URL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_STOP_ID, description={"suggested_value": "7000002"}): str,
    }
)



def _extract_unique_values(items: list[dict[str, Any]], key: str) -> list[str]:
    """Extract unique values from a list of dictionaries."""
    values = set()
    for item in items:
        value = item.get(key)
        if value and str(value).strip():
            values.add(str(value).strip())
    return sorted(list(values))


def _extract_schedule_filter_options(schedule_data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Extract route numbers and destinations from stopschedule data.
    
    Args:
        schedule_data: Full stopschedule response
        
    Returns:
        Tuple of (route_numbers, destinations)
    """
    routes = set()
    destinations = set()
    
    departure_times = schedule_data.get("departureTimes", {})
    
    # Iterate through all time buckets
    for time_bucket, departures in departure_times.items():
        for departure in departures:
            direction_info = departure.get("directionOfLine", {})
            
            # Extract route number
            line_number = direction_info.get("lineNumber")
            if line_number:
                routes.add(str(line_number))
            
            # Extract destination
            destination = direction_info.get("destinationName")
            if destination:
                destinations.add(str(destination))
    
    return sorted(list(routes)), sorted(list(destinations))


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tasmanian Transport."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._stop_id: str | None = None
        self._stop_name: str | None = None
        self._available_routes: list[str] = []
        self._available_destinations: list[str] = []

    def _extract_filter_options(self, schedule_data: dict[str, Any]) -> None:
        """Extract available route numbers and destinations from stopschedule data."""
        self._available_routes, self._available_destinations = _extract_schedule_filter_options(schedule_data)
        
        _LOGGER.debug("Extracted %d routes: %s", len(self._available_routes), self._available_routes)
        _LOGGER.debug("Extracted %d destinations: %s", len(self._available_destinations), self._available_destinations)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        
        errors: dict[str, str] = {}
        description_placeholders = {
            "transport_site_url": TRANSPORT_WEB_URL,
            "stop_finder_url": TRANSPORT_WEB_URL + "7109023",
            "instructions": "Stop IDs consist of your postcode followed by three digits (e.g., 7109023 for postcode 7109). To find your stop ID, visit the Tasmanian Transport website, search for your stop, and copy the ID from the URL.",
        }

        if user_input is not None:
            # Set unique ID and abort if already exists
            await self.async_set_unique_id(user_input[CONF_STOP_ID])
            self._abort_if_unique_id_configured()
            
            try:
                # Validate the stop ID and get stop information
                api = TasTransitApi()
                stop_info = await api.get_stop_info(user_input[CONF_STOP_ID])

                if not stop_info:
                    errors[CONF_STOP_ID] = "stop_not_found"
                else:
                    # Extract stop name from the API response
                    stop_name = "Unknown Stop"
                    if "stop" in stop_info and "name" in stop_info["stop"]:
                        stop_name = stop_info["stop"]["name"]
                    elif "name" in stop_info:
                        stop_name = stop_info["name"]
                    else:
                        stop_name = f"Stop {user_input[CONF_STOP_ID]}"

                    # Fetch schedule data to get available routes and destinations
                    try:
                        schedule_data = await api.get_stop_schedule(user_input[CONF_STOP_ID])
                        if schedule_data:
                            self._extract_filter_options(schedule_data)
                        else:
                            _LOGGER.warning("No schedule data returned for stop %s", user_input[CONF_STOP_ID])
                    except Exception as e:
                        _LOGGER.warning("Could not fetch schedule data for filter options: %s", e)
                        # Continue anyway with empty options
                    
                    # Store stop info and proceed to filter configuration
                    self._stop_id = user_input[CONF_STOP_ID]
                    self._stop_name = stop_name
                    return await self.async_step_filters()

            except Exception as exception:
                _LOGGER.exception("Unexpected exception: %s", exception)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders=description_placeholders,
        )
            
        errors: dict[str, str] = {}
        description_placeholders = {
            "transport_site_url": TRANSPORT_WEB_URL,
            "stop_finder_url": TRANSPORT_WEB_URL + "7109023",
            "instructions": "Stop IDs consist of your postcode followed by three digits (e.g., 7109023 for postcode 7109). To find your stop ID, visit the Tasmanian Transport website, search for your stop, and copy the ID from the URL.",
        }

        if user_input is not None:
            try:
                # Validate the stop ID and get stop information
                api = TasTransitApi()
                stop_info = await api.get_stop_info(user_input[CONF_STOP_ID])

                if not stop_info:
                    errors[CONF_STOP_ID] = "stop_not_found"
                else:
                    # Extract stop name from the API response
                    stop_name = "Unknown Stop"
                    if "stop" in stop_info and "name" in stop_info["stop"]:
                        stop_name = stop_info["stop"]["name"]
                    elif "name" in stop_info:
                        stop_name = stop_info["name"]
                    else:
                        stop_name = f"Stop {user_input[CONF_STOP_ID]}"

                    # Fetch schedule data to get available routes and destinations
                    try:
                        schedule_data = await api.get_stop_schedule(user_input[CONF_STOP_ID])
                        if schedule_data:
                            self._extract_filter_options(schedule_data)
                        else:
                            _LOGGER.warning("No schedule data returned for stop %s", user_input[CONF_STOP_ID])
                    except Exception as e:
                        _LOGGER.warning("Could not fetch schedule data for filter options: %s", e)
                        # Continue anyway with empty options
                    
                    # Store stop info and proceed to filter configuration
                    self._stop_id = user_input[CONF_STOP_ID]
                    self._stop_name = stop_name
                    return await self.async_step_filters()

            except Exception as exception:
                _LOGGER.exception("Unexpected exception: %s", exception)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the filter configuration step."""
        if user_input is not None:
            # Get selected filters from multi-select
            line_filters = user_input.get(CONF_LINE_FILTERS, [])
            destination_filters = user_input.get(CONF_DESTINATION_FILTERS, [])
            filter_mode = user_input.get(CONF_FILTER_MODE, FILTER_MODE_INCLUDE)

            
            # Create the stop configuration
            data = {
                CONF_STOP_ID: self._stop_id,
                CONF_STOP_NAME: self._stop_name,
            }

            # Add filters if provided
            if line_filters:
                data[CONF_LINE_FILTERS] = line_filters
            if destination_filters:
                data[CONF_DESTINATION_FILTERS] = destination_filters
            if line_filters or destination_filters:
                data[CONF_FILTER_MODE] = filter_mode

            return self.async_create_entry(
                title=self._stop_name,
                data=data,
            )

        # Build dynamic filter schema based on available options
        filter_schema_dict = {}
        
        # Add route filter if routes are available
        if self._available_routes:
            filter_schema_dict[vol.Optional(CONF_LINE_FILTERS)] = cv.multi_select(self._available_routes)
        
        # Add destination filter if destinations are available  
        if self._available_destinations:
            filter_schema_dict[vol.Optional(CONF_DESTINATION_FILTERS)] = cv.multi_select(self._available_destinations)
        
        # Always add filter mode option if we have any filters
        if self._available_routes or self._available_destinations:
            filter_schema_dict[vol.Optional(CONF_FILTER_MODE, default=FILTER_MODE_INCLUDE)] = vol.In([FILTER_MODE_INCLUDE, FILTER_MODE_EXCLUDE])
        
        # If no filters are available, create a minimal schema to allow proceeding
        if not filter_schema_dict:
            filter_schema_dict[vol.Optional("no_filters_available", default=True)] = bool
        
        filter_schema = vol.Schema(filter_schema_dict)

        # Show filter configuration form
        route_count = len(self._available_routes)
        dest_count = len(self._available_destinations)
        description_placeholders = {
            "stop_name": self._stop_name,
            "route_info": f"Found {route_count} route(s): {', '.join(self._available_routes[:5])}" + ("..." if route_count > 5 else "") if self._available_routes else "No routes found at this stop",
            "dest_info": f"Found {dest_count} destination(s): {', '.join(self._available_destinations[:3])}" + ("..." if dest_count > 3 else "") if self._available_destinations else "No destinations found at this stop",
        }

        return self.async_show_form(
            step_id="filters",
            data_schema=filter_schema,
            description_placeholders=description_placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Handle an options flow for Tasmanian Transport."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self._stop_id: str = self.config_entry.data[CONF_STOP_ID]
        self._stop_name: str = self.config_entry.data[CONF_STOP_NAME]
        self._available_routes: list[str] = []
        self._available_destinations: list[str] = []

    def _extract_filter_options(self, schedule_data: dict[str, Any]) -> None:
        """Extract available route numbers and destinations from stopschedule data."""
        self._available_routes, self._available_destinations = _extract_schedule_filter_options(schedule_data)
        
        _LOGGER.debug("Extracted %d routes: %s", len(self._available_routes), self._available_routes)
        _LOGGER.debug("Extracted %d destinations: %s", len(self._available_destinations), self._available_destinations)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        # Fetch schedule data to get available routes and destinations
        try:
            api = TasTransitApi()
            schedule_data = await api.get_stop_schedule(self._stop_id)
            if schedule_data:
                self._extract_filter_options(schedule_data)
            else:
                _LOGGER.warning("No schedule data returned for stop %s", self._stop_id)
        except Exception as e:
            _LOGGER.warning("Could not fetch schedule data for filter options: %s", e)
            # Continue anyway with empty options

        return await self.async_step_filters()

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the filter configuration step."""
        if user_input is not None:
            # Get selected filters from multi-select
            line_filters = user_input.get(CONF_LINE_FILTERS, [])
            destination_filters = user_input.get(CONF_DESTINATION_FILTERS, [])
            filter_mode = user_input.get(CONF_FILTER_MODE, FILTER_MODE_INCLUDE)

            # Create the options dictionary
            options = {
                CONF_LINE_FILTERS: line_filters,
                CONF_DESTINATION_FILTERS: destination_filters,
                CONF_FILTER_MODE: filter_mode,
            }

            return self.async_create_entry(title="", data=options)

        # Build dynamic filter schema based on available options
        current_options = self.config_entry.options
        filter_schema_dict = {}
        
        # Add route filter if routes are available
        if self._available_routes:
            filter_schema_dict[vol.Optional(CONF_LINE_FILTERS, default=current_options.get(CONF_LINE_FILTERS, []))] = cv.multi_select(self._available_routes)
        
        # Add destination filter if destinations are available  
        if self._available_destinations:
            filter_schema_dict[vol.Optional(CONF_DESTINATION_FILTERS, default=current_options.get(CONF_DESTINATION_FILTERS, []))] = cv.multi_select(self._available_destinations)
        
        # Always add filter mode option if we have any filters
        if self._available_routes or self._available_destinations:
            filter_schema_dict[vol.Optional(CONF_FILTER_MODE, default=current_options.get(CONF_FILTER_MODE, FILTER_MODE_INCLUDE))] = vol.In([FILTER_MODE_INCLUDE, FILTER_MODE_EXCLUDE])
        
        # If no filters are available, create a minimal schema to allow proceeding
        if not filter_schema_dict:
            filter_schema_dict[vol.Optional("no_filters_available", default=True)] = bool
        
        filter_schema = vol.Schema(filter_schema_dict)

        # Show filter configuration form
        route_count = len(self._available_routes)
        dest_count = len(self._available_destinations)
        description_placeholders = {
            "stop_name": self._stop_name,
            "route_info": f"Found {route_count} route(s): {', '.join(self._available_routes[:5])}" + ("..." if route_count > 5 else "") if self._available_routes else "No routes found at this stop",
            "dest_info": f"Found {dest_count} destination(s): {', '.join(self._available_destinations[:3])}" + ("..." if dest_count > 3 else "") if self._available_destinations else "No destinations found at this stop",
        }

        return self.async_show_form(
            step_id="filters",
            data_schema=filter_schema,
            description_placeholders=description_placeholders,
        )

