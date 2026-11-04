"""Low level functionality for interacting with Proflame fireplaces."""
import asyncio
from collections.abc import Callable
import json
from json.decoder import JSONDecodeError
import logging
import time

from websockets import ConnectionClosed
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidHandshake, InvalidURI

from .const import (
    CLOSE_TIMEOUT,
    CONNECT_TIMEOUT,
    DEFAULT_PORT,
    HANDSHAKE_TIMEOUT,
    KEEPALIVE_INTERVAL,
    KEEPALIVE_TIMEOUT,
    MAX_TEMPERATURE,
    MIN_TEMPERATURE,
    RECONNECT_MAX_DELAY,
    RECONNECT_MIN_DELAY,
    REQUIRED_STATE_RANGES,
    SEND_TIMEOUT,
    ApiAttrs,
    ApiControl,
)

_LOGGER = logging.getLogger(__name__)


class ProflameConnectionLost(Exception):
    """Raised when the fireplace stops answering keepalive pings."""


class ProflameClientBase:
    """Client used for interacting with Proflame fireplaces."""

    @staticmethod
    async def test_connection(host: str, port: int | None = None) -> bool:
        """Test the connection to the fireplace."""

        client = ProflameClientBase("probe", host, port)
        try:
            client._ws = await connect(
                client.uri, open_timeout=CONNECT_TIMEOUT, close_timeout=CLOSE_TIMEOUT
            )
            async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                await client._send(ApiControl.CONN_SYN)
                response = await client._ws.recv()

                if response == ApiControl.CONN_ACK:
                    _LOGGER.debug("Proflame connection to '%s' established", client.uri)
                    return True
                else:
                    msg = "Proflame connection test to '%s' failed with unexpected response (%s)"
                    _LOGGER.error(msg, client.uri, response)
                    return False
        except (OSError, asyncio.TimeoutError) as err:
            msg = "Proflame connection test to '%s' unavailable: %s"
            _LOGGER.debug(msg, client.uri, err)
            return False
        except Exception:  # pylint: disable=broad-exception-caught
            msg = "Encountered error while testing Proflame connection '%s'"
            _LOGGER.exception(msg, client.uri)
            return False
        finally:
            await client._close_socket()

    def __init__(
        self, device_id, host, port=None, logger=None, auto_reconnect=True
    ) -> None:
        """Create new class instance."""
        self._auto_reconnect = auto_reconnect
        self._device_id = device_id
        self._host = host
        self._port = port or DEFAULT_PORT
        self._logger = logger or _LOGGER
        self._callbacks = []
        self._connection_callbacks: list[Callable[[bool], None]] = []

        self._ws = None
        self._shutdown = False
        self._queue = asyncio.Queue()
        self._connection = None
        self._connected = False
        self._last_received = 0.0

        self._state = {}

    def __enter__(self):
        """Initiate a connection for a context manager."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        """Clean up on context manager exit."""
        self.close()

    async def _connect(self) -> None:
        """Maintain an open connection to the websocket, reopening it on failure.

        Each iteration opens one websocket and runs the send, receive and
        keepalive loops for it. As soon as any of those loops ends - because
        the fireplace closed or reset the socket, or stopped answering pings -
        the websocket is torn down and a fresh one is opened after a backoff.
        """
        delay = RECONNECT_MIN_DELAY
        while not self._shutdown:
            try:
                self._ws = await connect(
                    self.uri,
                    ping_interval=None,
                    open_timeout=CONNECT_TIMEOUT,
                    close_timeout=CLOSE_TIMEOUT,
                )
                self._state.clear()
                async with asyncio.timeout(HANDSHAKE_TIMEOUT):
                    await self._send(ApiControl.CONN_SYN)
                    acknowledged = False
                    while not (acknowledged and self._has_valid_baseline()):
                        message = await self._ws.recv()
                        acknowledged |= message == ApiControl.CONN_ACK
                        self._handle_message(message)
                self._last_received = time.monotonic()
                # Publish only after both ACK and a complete validated baseline.
                # Every callback, including state tracking, sees the whole state.
                self._publish_state(self._state)
                self._set_connected(True)
                delay = RECONNECT_MIN_DELAY
                await self._run_connection()
                self._warning("Connection closed by fireplace")
            except ConnectionClosed as err:
                self._warning("Connection lost: %s", err)
            except ProflameConnectionLost as err:
                self._warning("%s", err)
            except (OSError, TimeoutError, InvalidHandshake, InvalidURI) as err:
                self._warning("Unable to connect to fireplace: %s", err)
            except Exception:  # pylint: disable=broad-exception-caught
                self._exception("Unexpected error in fireplace connection")
            finally:
                self._set_connected(False)
                await self._close_socket()

            if self._shutdown or not self._auto_reconnect:
                break
            self._info("Reconnecting in %s seconds", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _run_connection(self) -> None:
        """Run the per-connection loops until the first one ends.

        A loop ending always means the connection is unusable, so the others
        are cancelled and the exception that ended it (if any) is re-raised
        for ``_connect`` to report.
        """

        async def run_worker(worker):
            try:
                await worker()
            finally:
                # Reject commands before cancellation or socket close can yield.
                self._set_connected(False)

        tasks = [
            asyncio.create_task(run_worker(worker))
            for worker in (self._dispatcher, self._listener, self._keepalive)
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            self._set_connected(False)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatcher(self) -> None:
        """Send queued commands over the current connection."""
        while True:
            item = await self._queue.get()
            try:
                if self.connected:
                    await self._send(json.dumps(item))
            finally:
                self._queue.task_done()

    def _handle_control_message(self, message):
        """Process a system control/info message from the websocket."""
        if message == ApiControl.CONN_ACK:
            self._debug("Connection acknowledged")
        elif message == ApiControl.PONG:
            self._debug("Ping acknowledged")
        else:
            self._warning("Received unexpected control message (%s)", message)

    def _handle_json_message(self, message) -> None:
        """Process a system state message from the websocket."""
        if not isinstance(message, dict):
            self._warning(
                "Received unexpected JSON message (%s) - %s",
                "NOT_AN_OBJECT",
                json.dumps(message),
            )
            return
        for k, v in message.items():
            if k in REQUIRED_STATE_RANGES:
                bounds = REQUIRED_STATE_RANGES[k]
                if (
                    not isinstance(v, int)
                    or isinstance(v, bool)
                    or (bounds is not None and not bounds[0] <= v <= bounds[1])
                ):
                    self._warning("Invalid control state for '%s': %s", k, v)
                    self._state.pop(k, None)
                    continue
            if not isinstance(v, int | str):
                self._warning(
                    "Skipping unsupported value type for key '%s': %s", k, type(v)
                )
                continue
            self._state[k] = v
        if not self.connected:
            return
        if not self._has_valid_baseline():
            self._set_connected(False)
            raise ProflameConnectionLost("Invalid control state received")
        self._publish_state(message)

    def _has_valid_baseline(self) -> bool:
        """Require usable state for every enabled entity, not just metadata."""
        if not all(key in self._state for key in REQUIRED_STATE_RANGES):
            return False
        unit = self._state[ApiAttrs.TEMPERATURE_UNIT]
        target = self._state[ApiAttrs.TARGET_TEMPERATURE]
        if unit == 0:
            low, high = MIN_TEMPERATURE.to_celcius(), MAX_TEMPERATURE.to_celcius()
        else:
            low, high = MIN_TEMPERATURE.to_fahrenheit(), MAX_TEMPERATURE.to_fahrenheit()
        return low * 10 <= target <= high * 10

    def _publish_state(self, fields) -> None:
        """Notify consumers after all fields of a message have been applied."""
        for k in fields:
            if k not in self._state:
                continue
            v = self._state[k]
            for callback in self._callbacks:
                try:
                    callback(k, v)
                except Exception:  # pylint: disable=broad-exception-caught
                    self._exception("Error in state callback")

    def _handle_message(self, message):
        """Process a message from the websocket."""
        try:
            self._handle_json_message(json.loads(message))
        except JSONDecodeError:
            self._handle_control_message(message)

    async def _keepalive(self) -> None:
        """Ping the fireplace periodically and fail once it stops answering.

        The fireplace replies to every PROFLAMEPING with PROFLAMEPONG, so a
        long silence means the socket is dead even if the kernel has not
        noticed yet (for example after a silent Wi-Fi drop).
        """
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            silence = time.monotonic() - self._last_received
            if silence > KEEPALIVE_TIMEOUT:
                raise ProflameConnectionLost(
                    f"No response from fireplace for {silence:.0f} seconds"
                )
            await self._send(ApiControl.PING)

    async def _listener(self) -> None:
        """Receive messages until the connection closes."""
        async for message in self._ws:
            self._last_received = time.monotonic()
            self._debug("RECV: %s", message)
            try:
                self._handle_message(message)
            except ProflameConnectionLost:
                raise
            except Exception:  # pylint: disable=broad-exception-caught
                self._exception("Error handling message from fireplace")

    async def _send(self, message) -> None:
        """Send message to the fireplace websocket."""
        self._debug("SEND: %s", message)
        async with asyncio.timeout(SEND_TIMEOUT):
            await self._ws.send(message)

    async def _close_socket(self) -> None:
        """Bound graceful teardown, aborting the transport if it stalls."""
        websocket, self._ws = self._ws, None
        if websocket is None:
            return
        close_task = asyncio.create_task(websocket.close())
        try:
            # Legacy websockets.close() can swallow cancellation. Shield it so
            # our timeout fires before aborting the transport and cancelling it.
            await asyncio.wait_for(asyncio.shield(close_task), CLOSE_TIMEOUT)
        except (TimeoutError, ConnectionClosed):
            websocket.transport.abort()
        except asyncio.CancelledError:
            websocket.transport.abort()
            raise
        finally:
            close_task.cancel()
            await asyncio.gather(close_task, return_exceptions=True)

    def _set_connected(self, connected: bool) -> None:
        """Record the connection state and notify listeners when it changes."""
        if connected == self._connected:
            return
        self._connected = connected
        if connected:
            self._info("Connected to fireplace")
        else:
            self._discard_queued_commands()
        for callback in self._connection_callbacks:
            try:
                callback(connected)
            except Exception:  # pylint: disable=broad-exception-caught
                self._exception("Error in connection callback")

    def _discard_queued_commands(self) -> None:
        """Drop commands that were queued but never sent.

        Commands are only delivered over a live connection; replaying stale
        ones after a reconnect could light the fireplace long after the user
        asked for it.
        """
        dropped = 0
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
            dropped += 1
        if dropped:
            self._warning("Dropped %d unsent command(s) after connection loss", dropped)

    async def close(self) -> None:
        """Close the websocket connection and stop reconnecting."""
        self._debug("Connection closing")
        self._shutdown = True
        self._set_connected(False)
        if self._connection:
            self._connection.cancel()
            await asyncio.gather(self._connection, return_exceptions=True)
            self._connection = None
        await self._close_socket()
        self._debug("Connection closed")

    def get_state(self, field: str) -> int | None:
        """Query the state of the fireplace."""
        return self._state.get(field, None)

    async def open(self) -> None:
        """Connect to the Proflame websocket."""
        self._debug("Connection opening")
        if self._connection is not None and not self._connection.done():
            return
        self._shutdown = False
        self._connection = asyncio.create_task(self._connect())

    def register_callback(self, callback) -> None:
        """Register a callback that will be triggered on state changes."""
        self._callbacks.append(callback)

    def register_connection_callback(self, callback: Callable[[bool], None]) -> None:
        """Register a callback that will be triggered when connectivity changes."""
        self._connection_callbacks.append(callback)

    def set_state(self, field: str, value: int) -> None:
        """Send a state update to the fireplace."""
        if not self._connected:
            self._warning(
                "Not connected to fireplace; dropping command %s=%s", field, value
            )
            return
        self._queue.put_nowait({field: value})

    def _debug(self, msg, *args) -> None:
        """Shortcut for debug logging."""
        formatted = f"PF[{self._host}] {msg}"
        self._logger.debug(formatted, *args)

    def _error(self, msg, *args) -> None:
        """Shortcut for error logging."""
        formatted = f"PF[{self._host}] {msg}"
        self._logger.error(formatted, *args)

    def _exception(self, msg, *args) -> None:
        """Shortcut for exception logging."""
        formatted = f"PF[{self._host}] {msg}"
        self._logger.exception(formatted, *args)

    def _info(self, msg, *args) -> None:
        """Shortcut for info logging."""
        formatted = f"PF[{self._host}] {msg}"
        self._logger.info(formatted, *args)

    def _warning(self, msg, *args) -> None:
        """Shortcut for warning logging."""
        formatted = f"PF[{self._host}] {msg}"
        self._logger.warning(formatted, *args)

    @property
    def connected(self) -> bool:
        """Whether the protocol is acknowledged and fresh state is available."""
        return self._connected

    @property
    def device_id(self) -> str:
        """Retrieve the unique ID of the device."""
        return self._device_id

    @property
    def full_state(self) -> dict[str, int]:
        """Retrieve full copy of all know fireplace state."""
        return {**self._state}

    @property
    def uri(self):
        """The formatted URI for connecting to the fireplace websocket."""
        return f"ws://{self._host}:{self._port}"
