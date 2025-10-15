"""WebSocket client for Tasmanian Transport real-time vehicle tracking."""
from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from typing import Any, Callable

import aiohttp
from aiohttp import WSMsgType

_LOGGER = logging.getLogger(__name__)

# WebSocket endpoint for real-time vehicle data
WEBSOCKET_URL = "wss://real-time.transport.tas.gov.au/timetable/websocket/all?map"

# Reconnection settings
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY_BASE = 5  # seconds
MAX_RECONNECT_DELAY = 300  # 5 minutes


class VehicleMessageType(Enum):
    """Types of vehicle messages received from WebSocket."""
    APPROACHING = "APPROACHING"
    REMOVED = "REMOVED"


class WebSocketState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class TasTransitWebSocketClient:
    """WebSocket client for real-time vehicle tracking."""

    def __init__(self, vehicle_callback: Callable[[dict[str, Any]], None]) -> None:
        """Initialize WebSocket client.

        Args:
            vehicle_callback: Function to call when vehicle data is received
        """
        self._vehicle_callback = vehicle_callback
        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._state = WebSocketState.DISCONNECTED
        self._reconnect_attempts = 0
        self._reconnect_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None
        self._subscribed_stops: set[str] = set()
        self._running = False

    @property
    def is_connected(self) -> bool:
        """Return True if WebSocket is connected."""
        return self._state == WebSocketState.CONNECTED and self._websocket is not None

    @property
    def state(self) -> WebSocketState:
        """Return current connection state."""
        return self._state

    async def connect(self) -> bool:
        """Connect to WebSocket server."""
        if self._state in (WebSocketState.CONNECTING, WebSocketState.CONNECTED):
            return True

        self._state = WebSocketState.CONNECTING
        _LOGGER.info("Connecting to WebSocket at %s", WEBSOCKET_URL)

        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()

            self._websocket = await self._session.ws_connect(
                WEBSOCKET_URL,
                timeout=30,
                heartbeat=60,
            )

            if self._websocket.closed:
                _LOGGER.error("WebSocket connection failed immediately")
                self._state = WebSocketState.FAILED
                return False

            self._state = WebSocketState.CONNECTED
            self._reconnect_attempts = 0
            _LOGGER.info("WebSocket connected successfully")

            # Start listening for messages
            if self._listen_task is None or self._listen_task.done():
                self._listen_task = asyncio.create_task(self._listen_for_messages())

            # Re-subscribe to any previously subscribed stops
            if self._subscribed_stops:
                _LOGGER.info("Re-subscribing to %d stops", len(self._subscribed_stops))
                for stop_id in self._subscribed_stops.copy():
                    await self._send_subscription(stop_id)

            return True

        except Exception as err:
            _LOGGER.error("WebSocket connection failed: %s", err)
            self._state = WebSocketState.FAILED
            await self._cleanup_connection()
            return False

    async def disconnect(self) -> None:
        """Disconnect from WebSocket server."""
        _LOGGER.info("Disconnecting WebSocket client")
        self._running = False

        # Cancel tasks
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        await self._cleanup_connection()
        self._state = WebSocketState.DISCONNECTED

    async def subscribe_to_stop(self, stop_id: str) -> bool:
        """Subscribe to real-time updates for a specific stop.

        Args:
            stop_id: Bus stop ID to subscribe to

        Returns:
            True if subscription was successful
        """
        self._subscribed_stops.add(stop_id)

        if not self.is_connected:
            _LOGGER.info("WebSocket not connected, subscription to %s will be sent when connected", stop_id)
            return True

        return await self._send_subscription(stop_id)

    async def unsubscribe_from_stop(self, stop_id: str) -> None:
        """Unsubscribe from updates for a specific stop.

        Args:
            stop_id: Bus stop ID to unsubscribe from
        """
        self._subscribed_stops.discard(stop_id)
        # Note: The API doesn't seem to have an explicit unsubscribe mechanism
        _LOGGER.debug("Unsubscribed from stop %s (local only)", stop_id)

    async def start(self) -> None:
        """Start the WebSocket client with auto-reconnection."""
        _LOGGER.info("Starting WebSocket client")
        self._running = True

        # Initial connection
        if not await self.connect():
            # Schedule reconnection
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._handle_reconnection())

    async def _send_subscription(self, stop_id: str) -> bool:
        """Send subscription message for a stop."""
        if not self._websocket or self._websocket.closed:
            return False

        try:
            subscription_message = f"V_{stop_id}"
            await self._websocket.send_str(subscription_message)
            _LOGGER.debug("Sent subscription for stop %s", stop_id)
            return True
        except Exception as err:
            _LOGGER.error("Failed to send subscription for stop %s: %s", stop_id, err)
            return False

    async def _listen_for_messages(self) -> None:
        """Listen for incoming WebSocket messages."""
        _LOGGER.debug("Started listening for WebSocket messages")

        try:
            async for msg in self._websocket:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", self._websocket.exception())
                    break
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING):
                    _LOGGER.info("WebSocket connection closed")
                    break

        except asyncio.CancelledError:
            _LOGGER.debug("WebSocket message listener cancelled")
            raise
        except Exception as err:
            _LOGGER.error("Error in WebSocket message listener: %s", err)
        finally:
            _LOGGER.debug("WebSocket message listener stopped")

        # Connection lost, schedule reconnection
        if self._running and self._state == WebSocketState.CONNECTED:
            self._state = WebSocketState.RECONNECTING
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._handle_reconnection())

    async def _handle_message(self, raw_message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            # Messages can contain multiple V_ prefixed JSON objects concatenated together
            # Split by "V_" but keep the delimiter
            if not raw_message.startswith("V_"):
                _LOGGER.debug("Ignoring non-vehicle message: %s", raw_message[:50])
                return

            # Split the message into individual vehicle messages
            message_parts = raw_message.split("V_")[1:]  # Skip the empty first element

            for json_part in message_parts:
                if not json_part.strip():
                    continue

                # Clean up the JSON part - sometimes there are pipe characters
                json_part = json_part.split("|")[0]  # Take only the first JSON object
                json_part = json_part.strip()

                if not json_part:
                    continue

                try:
                    vehicle_data = json.loads(json_part)
                except json.JSONDecodeError as err:
                    _LOGGER.debug("Failed to decode vehicle JSON part '%s': %s", json_part[:100], err)
                    continue

                # Validate required fields
                if not isinstance(vehicle_data, dict):
                    _LOGGER.debug("Invalid vehicle data format: %s", type(vehicle_data))
                    continue

                message_type = vehicle_data.get("type")
                if message_type not in [t.value for t in VehicleMessageType]:
                    _LOGGER.debug("Unknown message type: %s", message_type)
                    continue

                _LOGGER.debug("Received %s message for vehicle %s",
                             message_type, vehicle_data.get("vehicleId"))

                # Call the callback with processed vehicle data
                self._vehicle_callback(vehicle_data)

        except Exception as err:
            _LOGGER.error("Error handling WebSocket message: %s", err)

    async def _handle_reconnection(self) -> None:
        """Handle automatic reconnection with exponential backoff."""
        while self._running and self._state != WebSocketState.CONNECTED:
            if self._reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                _LOGGER.error("Maximum reconnection attempts reached, giving up")
                self._state = WebSocketState.FAILED
                break

            # Calculate delay with exponential backoff
            delay = min(RECONNECT_DELAY_BASE * (2 ** self._reconnect_attempts), MAX_RECONNECT_DELAY)
            self._reconnect_attempts += 1

            _LOGGER.info("Attempting reconnection #%d in %d seconds",
                        self._reconnect_attempts, delay)

            try:
                await asyncio.sleep(delay)

                if not self._running:
                    break

                if await self.connect():
                    _LOGGER.info("WebSocket reconnected successfully")
                    break

            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Reconnection attempt failed: %s", err)

    async def _cleanup_connection(self) -> None:
        """Clean up WebSocket connection and session."""
        if self._websocket and not self._websocket.closed:
            await self._websocket.close()
        self._websocket = None

        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None