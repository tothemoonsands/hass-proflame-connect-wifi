"""Tests for the connection handling in ProflameClientBase."""
import asyncio
import json
import logging
from unittest.mock import MagicMock, Mock, patch

from proflame_test_support import BASELINE_STATE
import pytest
import pytest_asyncio
from websockets.exceptions import ConnectionClosedError, InvalidHandshake

from custom_components.proflame_connect_wifi import client_base
from custom_components.proflame_connect_wifi.client_base import (
    ProflameClientBase,
    ProflameConnectionLost,
)
from custom_components.proflame_connect_wifi.const import ApiControl

pytestmark = pytest.mark.asyncio


class FakeWebSocket:
    """Scripted stand-in for a websocket connection.

    Items in ``script`` are delivered to the receive loop in order. Strings are
    returned as messages; exceptions are raised from the iterator (use
    ``ConnectionClosedError`` for a reset and ``StopAsyncIteration`` for a clean
    close). Once the script is exhausted the iterator blocks.
    """

    def __init__(self, script=(), ready=True) -> None:
        """Create a fake websocket with a scripted inbound stream."""
        self.sent: list[str] = []
        self.closed = False
        self.incoming: asyncio.Queue = asyncio.Queue()
        if ready:
            self.incoming.put_nowait(ApiControl.CONN_ACK)
            self.incoming.put_nowait(json.dumps(BASELINE_STATE))
        for item in script:
            self.incoming.put_nowait(item)

    def deliver(self, item) -> None:
        """Queue another inbound item after construction."""
        self.incoming.put_nowait(item)

    async def send(self, message) -> None:
        """Record an outbound message."""
        if self.closed:
            raise ConnectionClosedError(None, None)
        self.sent.append(message)

    def __aiter__(self):
        """Iterate over inbound messages."""
        return self

    async def __anext__(self):
        """Return the next scripted item or raise it if it is an exception."""
        item = await self.incoming.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def recv(self):
        """Read a protocol handshake message."""
        return await self.__anext__()

    async def close(self) -> None:
        """Mark the socket closed."""
        self.closed = True


class FakeConnect:
    """Async context manager returned by the patched ``connect``."""

    def __init__(self, outcome) -> None:
        """Wrap either a FakeWebSocket or an exception to raise on entry."""
        self.outcome = outcome

    def __await__(self):
        """Support explicit websocket ownership."""
        return self.__aenter__().__await__()

    async def __aenter__(self):
        """Yield the websocket or fail the handshake."""
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, exc_type, exc, tb):
        """Close the websocket when the connection block exits."""
        await self.outcome.close()
        return False


class ConnectScript:
    """Replacement for ``websockets.client.connect`` with scripted outcomes."""

    def __init__(self, *outcomes) -> None:
        """Store the outcomes returned by successive connect calls."""
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, uri, **kwargs):
        """Return the next scripted connection."""
        self.calls.append((uri, kwargs))
        if self.outcomes:
            return FakeConnect(self.outcomes.pop(0))
        return FakeConnect(FakeWebSocket())


async def wait_until(predicate, timeout: float = 2.0) -> None:
    """Poll ``predicate`` until it is true or fail after ``timeout`` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


@pytest.fixture
def fast_timing():
    """Shrink reconnect and keepalive timings so tests run quickly."""
    with patch.object(client_base, "RECONNECT_MIN_DELAY", 0.01), patch.object(
        client_base, "RECONNECT_MAX_DELAY", 0.02
    ), patch.object(client_base, "KEEPALIVE_INTERVAL", 0.02), patch.object(
        client_base, "KEEPALIVE_TIMEOUT", 10
    ):
        yield


@pytest.fixture
def connections():
    """Track connection callback invocations."""
    return []


@pytest_asyncio.fixture
async def client(fast_timing, connections):
    """Create a client wired to record connection changes; closed on teardown."""
    instance = ProflameClientBase("device", "192.0.2.1")
    instance.register_connection_callback(connections.append)
    yield instance
    await instance.close()


def reset() -> ConnectionClosedError:
    """Build the exception websockets raises for a TCP reset."""
    return ConnectionClosedError(None, None)


async def test_reconnects_after_connection_reset(client, connections) -> None:
    """A reset socket is torn down and a fresh connection is opened."""
    first = FakeWebSocket([reset()])
    second = FakeWebSocket(['{"main_mode": 1}'])
    script = ConnectScript(first, second)

    with patch.object(client_base, "connect", script):
        await client.open()
        await wait_until(lambda: client.get_state("main_mode") == 1)

    assert len(script.calls) == 2
    assert client.connected
    assert first.closed
    assert first.sent[0] == ApiControl.CONN_SYN
    assert second.sent[0] == ApiControl.CONN_SYN
    assert connections == [True, False, True]


async def test_reconnects_after_clean_close(client, connections) -> None:
    """A clean close from the fireplace also triggers a reconnect."""
    first = FakeWebSocket([StopAsyncIteration()])
    second = FakeWebSocket()
    script = ConnectScript(first, second)

    with patch.object(client_base, "connect", script):
        await client.open()
        await wait_until(lambda: len(script.calls) == 2 and client.connected)

    assert connections == [True, False, True]


async def test_reconnects_when_send_fails(client, connections) -> None:
    """A send failure on a dead socket ends the connection and reconnects."""
    first = FakeWebSocket()
    second = FakeWebSocket()
    script = ConnectScript(first, second)

    with patch.object(client_base, "connect", script):
        await client.open()
        await wait_until(lambda: client.connected)
        first.closed = True  # the next keepalive or command send now fails
        await wait_until(lambda: len(script.calls) == 2 and client.connected)

    assert connections == [True, False, True]


async def test_reconnects_after_keepalive_timeout(client, connections) -> None:
    """Silence from the fireplace is treated as a dead connection."""
    first = FakeWebSocket()
    second = FakeWebSocket()
    script = ConnectScript(first, second)

    with patch.object(client_base, "KEEPALIVE_TIMEOUT", 0.05), patch.object(
        client_base, "connect", script
    ):
        await client.open()
        await wait_until(lambda: len(script.calls) == 2 and client.connected)

    assert ApiControl.PING in first.sent
    assert connections == [True, False, True]


async def test_inbound_messages_keep_connection_alive(client) -> None:
    """Regular traffic from the fireplace prevents the keepalive timeout."""
    first = FakeWebSocket()
    script = ConnectScript(first)

    with patch.object(client_base, "KEEPALIVE_TIMEOUT", 0.05), patch.object(
        client_base, "connect", script
    ):
        await client.open()
        await wait_until(lambda: client.connected)
        for _ in range(8):
            first.deliver(ApiControl.PONG)
            await asyncio.sleep(0.02)

    assert len(script.calls) == 1
    assert client.connected


async def test_retries_when_connect_fails(client, connections) -> None:
    """Connection errors during the handshake are retried."""
    second = FakeWebSocket()
    script = ConnectScript(OSError("connection refused"), InvalidHandshake(), second)

    with patch.object(client_base, "connect", script):
        await client.open()
        await wait_until(lambda: client.connected)

    assert len(script.calls) == 3
    assert connections == [True]


async def test_backoff_grows_and_resets(client) -> None:
    """Reconnect delay doubles up to the maximum and resets after success."""
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def record_sleep(delay):
        if delay >= 0.01:  # ignore the keepalive interval
            delays.append(delay)
        await real_sleep(0)

    script = ConnectScript(
        OSError(), OSError(), OSError(), FakeWebSocket([reset()]), OSError()
    )
    with patch.object(client_base, "RECONNECT_MIN_DELAY", 0.01), patch.object(
        client_base, "RECONNECT_MAX_DELAY", 0.03
    ), patch.object(client_base, "KEEPALIVE_INTERVAL", 0.001), patch.object(
        client_base.asyncio, "sleep", record_sleep
    ), patch.object(client_base, "connect", script):
        await client.open()
        await wait_until(lambda: len(script.calls) >= 6 and client.connected)

    # three failures: 0.01 -> 0.02 -> capped at 0.03; the successful fourth
    # connection resets the delay, so its reset is followed by 0.01 then 0.02.
    assert delays[:5] == pytest.approx([0.01, 0.02, 0.03, 0.01, 0.02])


async def test_commands_are_sent_while_connected(client) -> None:
    """Queued commands are delivered over the open connection."""
    socket = FakeWebSocket()

    with patch.object(client_base, "connect", ConnectScript(socket)):
        await client.open()
        await wait_until(lambda: client.connected)
        client.set_state("flame_control", 3)
        await wait_until(lambda: json.dumps({"flame_control": 3}) in socket.sent)


async def test_commands_dropped_while_disconnected(client, caplog) -> None:
    """Commands issued without a connection are dropped, not queued."""
    with caplog.at_level(logging.WARNING):
        client.set_state("flame_control", 3)

    assert client._queue.empty()
    assert "dropping command flame_control=3" in caplog.text


async def test_queued_commands_discarded_on_disconnect(client, caplog) -> None:
    """Unsent commands do not survive a connection loss."""
    client._set_connected(True)
    client.set_state("flame_control", 3)
    client.set_state("fan_control", 2)
    assert client._queue.qsize() == 2

    with caplog.at_level(logging.WARNING):
        client._set_connected(False)

    assert client._queue.empty()
    assert "Dropped 2 unsent command(s)" in caplog.text


async def test_handler_error_does_not_end_connection(client) -> None:
    """A failing state callback is logged without dropping the connection."""
    socket = FakeWebSocket(['{"main_mode": 1}', '{"fan_control": 2}'])
    script = ConnectScript(socket)

    def bad_callback(key, value):
        raise RuntimeError("boom")

    client.register_callback(bad_callback)
    with patch.object(client_base, "connect", script):
        await client.open()
        await wait_until(lambda: client.get_state("fan_control") == 2)

    assert client.get_state("main_mode") == 1
    assert client.connected
    assert len(script.calls) == 1


async def test_connection_callback_error_is_isolated(client, connections) -> None:
    """One failing connection callback does not block the others."""

    def bad_callback(connected):
        raise RuntimeError("boom")

    client._connection_callbacks.insert(0, bad_callback)
    client._set_connected(True)

    assert connections == [True]


async def test_close_stops_reconnecting(client, connections) -> None:
    """Closing the client ends the connection and prevents reconnects."""
    socket = FakeWebSocket()
    script = ConnectScript(socket)

    with patch.object(client_base, "connect", script):
        await client.open()
        await wait_until(lambda: client.connected)
        await client.close()
        await asyncio.sleep(0.05)

    assert socket.closed
    assert not client.connected
    assert connections == [True, False]
    assert len(script.calls) == 1


async def test_keepalive_raises_connection_lost() -> None:
    """The keepalive loop raises once the silence exceeds the timeout."""
    client = ProflameClientBase("device", "192.0.2.1")
    client._ws = FakeWebSocket()
    client._last_received = 0.0

    with patch.object(client_base, "KEEPALIVE_INTERVAL", 0), pytest.raises(
        ProflameConnectionLost
    ):
        await client._keepalive()


@pytest.mark.parametrize("state_first", [False, True])
async def test_requires_ack_and_fresh_state(client, state_first) -> None:
    """Neither transport open, pong, invalid JSON state nor ack alone is ready."""
    socket = FakeWebSocket(ready=False)
    with patch.object(client_base, "connect", ConnectScript(socket)):
        await client.open()
        await wait_until(lambda: bool(socket.sent))
        for message in (ApiControl.PONG, "{}", "[]", '{"main_mode": null}'):
            socket.deliver(message)
        first, second = ApiControl.CONN_ACK, json.dumps(BASELINE_STATE)
        if state_first:
            first, second = second, first
        socket.deliver(first)
        await wait_until(socket.incoming.empty)
        assert not client.connected
        client.set_state("flame_control", 5)
        socket.deliver(second)
        await wait_until(lambda: client.connected)
        assert client._queue.empty()
        assert not any("flame_control" in message for message in socket.sent)


async def test_handshake_timeout_retries(client, connections) -> None:
    """A peer that never acknowledges cannot hold startup indefinitely."""
    first = FakeWebSocket([ApiControl.PONG], ready=False)
    script = ConnectScript(first, FakeWebSocket())
    with patch.object(client_base, "HANDSHAKE_TIMEOUT", 0.03), patch.object(
        client_base, "connect", script
    ):
        await client.open()
        await wait_until(lambda: client.connected)
    assert first.closed
    assert len(script.calls) == 2
    assert connections == [True]


@pytest.mark.parametrize("explicit_close", [False, True])
async def test_unavailable_before_stalled_close(client, explicit_close) -> None:
    """Teardown rejects commands immediately and aborts a stuck close."""
    closing = asyncio.Event()
    first = FakeWebSocket()
    first.transport = Mock()

    async def stuck_close():
        closing.set()
        await asyncio.Future()

    first.close = stuck_close
    second = FakeWebSocket()
    script = ConnectScript(first, second)
    with patch.object(client_base, "CLOSE_TIMEOUT", 0.04), patch.object(
        client_base, "connect", script
    ):
        await client.open()
        await wait_until(lambda: client.connected)
        # No yield between queuing and detecting the failure/starting close.
        client.set_state("flame_control", 5)
        if explicit_close:
            close_task = asyncio.create_task(client.close())
        else:
            first.deliver(reset())
        await asyncio.wait_for(closing.wait(), 1)
        assert not client.connected
        client.set_state("fan_control", 6)
        assert client._queue.empty()
        if explicit_close:
            await asyncio.wait_for(close_task, 1)
            await client.open()
        await wait_until(lambda: len(script.calls) == 2 and client.connected)
        assert not any("control" in message for message in second.sent)
        first.transport.abort.assert_called_once()


async def test_send_timeout_reconnects_without_replay(client) -> None:
    """A blocked command send is cancelled and never retried on a new socket."""
    first, second = FakeWebSocket(), FakeWebSocket()
    script = ConnectScript(first, second)
    sending = asyncio.Event()

    async def stuck_send(message):
        sending.set()
        await asyncio.Future()

    with patch.object(client_base, "SEND_TIMEOUT", 0.03), patch.object(
        client_base, "connect", script
    ):
        await client.open()
        await wait_until(lambda: client.connected)
        first.send = stuck_send
        client.set_state("flame_control", 5)
        await asyncio.wait_for(sending.wait(), 1)
        client.set_state("fan_control", 6)
        await wait_until(lambda: len(script.calls) == 2 and client.connected)
        await asyncio.wait_for(client._queue.join(), 1)
        assert not any("control" in message for message in second.sent)


async def test_open_is_idempotent_and_close_allows_reopen(client) -> None:
    """Repeated opens do not create parallel connections; close is reusable."""
    script = ConnectScript(FakeWebSocket(), FakeWebSocket())
    with patch.object(client_base, "connect", script):
        await client.open()
        task = client._connection
        await client.open()
        assert client._connection is task
        await wait_until(lambda: client.connected)
        await client.close()
        assert task.done()
        await client.open()
        await wait_until(lambda: client.connected)
        assert len(script.calls) == 2


async def test_close_during_handshake_and_backoff(client) -> None:
    """Unload cancels protocol startup and reconnect sleeps without retrying."""
    socket = FakeWebSocket(ready=False)
    script = ConnectScript(socket)
    with patch.object(client_base, "connect", script):
        await client.open()
        await wait_until(lambda: bool(socket.sent))
        await asyncio.wait_for(client.close(), 1)
        assert socket.closed
        assert len(script.calls) == 1
    script = ConnectScript(OSError())
    with patch.object(client_base, "RECONNECT_MIN_DELAY", 60), patch.object(
        client_base, "connect", script
    ):
        await client.open()
        await wait_until(lambda: bool(script.calls))
        await asyncio.wait_for(client.close(), 1)
        assert len(script.calls) == 1


async def test_close_during_teardown_aborts_socket(client) -> None:
    """Unload during an already-running close must not orphan the transport."""
    socket = FakeWebSocket()
    socket.transport = Mock()
    closing = asyncio.Event()

    async def stuck_close():
        closing.set()
        await asyncio.Future()

    socket.close = stuck_close
    with patch.object(client_base, "connect", ConnectScript(socket)):
        await client.open()
        await wait_until(lambda: client.connected)
        socket.deliver(reset())
        await asyncio.wait_for(closing.wait(), 1)
        await asyncio.wait_for(client.close(), 1)
        socket.transport.abort.assert_called_once()
        assert not client.connected
        assert client._ws is None


async def test_close_timeout_survives_library_cancellation_handling(client) -> None:
    """A close implementation swallowing cancellation still gets aborted."""
    socket = FakeWebSocket()
    aborted = asyncio.Event()
    socket.transport = Mock()
    socket.transport.abort.side_effect = aborted.set

    async def stubborn_close():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await aborted.wait()

    socket.close = stubborn_close
    client._ws = socket
    with patch.object(client_base, "CLOSE_TIMEOUT", 0.03):
        await asyncio.wait_for(client._close_socket(), 1)
    socket.transport.abort.assert_called_once()


@pytest.mark.parametrize("field", BASELINE_STATE)
async def test_each_control_field_is_required(client, field) -> None:
    """Omitting any field leaves all controls unavailable until it arrives."""
    partial = {key: value for key, value in BASELINE_STATE.items() if key != field}
    socket = FakeWebSocket([ApiControl.CONN_ACK, json.dumps(partial)], ready=False)
    snapshots = []
    client.register_callback(lambda *_: snapshots.append(client.full_state))
    with patch.object(client_base, "connect", ConnectScript(socket)):
        await client.open()
        await wait_until(socket.incoming.empty)
        assert not client.connected
        assert snapshots == []
        socket.deliver(json.dumps({field: BASELINE_STATE[field]}))
        await wait_until(lambda: client.connected)
        assert snapshots
        assert all(snapshot == BASELINE_STATE for snapshot in snapshots)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("main_mode", -1),
        ("main_mode", 4),
        ("main_mode", True),
        ("flame_control", 7),
        ("fan_control", -1),
        ("lamp_control", "3"),
        ("pilot_mode", 2),
        ("temperature_unit", 2),
        ("temperature_unit", False),
        ("temperature_set", 700),  # Fahrenheit magnitude with Celsius unit
        ("temperature_set", 0),
        ("temperature_set", 22.0),
        ("room_temperature", None),
        ("room_temperature", "210"),
        ("room_temperature", True),
    ],
)
async def test_invalid_baseline_waits_for_correction(client, field, invalid) -> None:
    """Invalid types/ranges do not publish state or enable command delivery."""
    socket = FakeWebSocket(
        [ApiControl.CONN_ACK, json.dumps({**BASELINE_STATE, field: invalid})],
        ready=False,
    )
    snapshots = []
    client.register_callback(lambda *_: snapshots.append(client.full_state))
    with patch.object(client_base, "connect", ConnectScript(socket)):
        await client.open()
        await wait_until(socket.incoming.empty)
        assert not client.connected
        assert snapshots == []
        client.set_state("main_mode", 1)
        assert client._queue.empty()
        socket.deliver(json.dumps({field: BASELINE_STATE[field]}))
        await wait_until(lambda: client.connected)
        assert all(snapshot == BASELINE_STATE for snapshot in snapshots)


async def test_invalid_live_control_state_disconnects(client, connections) -> None:
    """A bad delta cannot keep controls available using the previous value."""
    socket = FakeWebSocket()
    snapshots = []
    client.register_callback(lambda *_: snapshots.append(client.full_state))
    client._auto_reconnect = False
    with patch.object(client_base, "connect", ConnectScript(socket)):
        await client.open()
        await wait_until(lambda: client.connected)
        snapshots.clear()
        socket.deliver('{"temperature_unit": 9}')
        await asyncio.wait_for(client._connection, 1)
        assert connections == [True, False]
        assert snapshots == []
        client.set_state("temperature_set", 700)
        assert client._queue.empty()


@pytest.mark.asyncio
async def test_test_connection_returns_false_on_connection_refused(monkeypatch):
    """Connection failures during validation should return False without exceptions."""

    def _raise_oserror(_uri, **_kwargs):
        raise ConnectionRefusedError("connect failed")

    monkeypatch.setattr(
        "custom_components.proflame_connect_wifi.client_base.connect",
        _raise_oserror,
    )

    assert await client_base.ProflameClientBase.test_connection("192.168.1.114", 88) is False


@pytest.mark.asyncio
async def test_test_connection_connection_refused_logs_debug(monkeypatch):
    """Refused sockets during probe should log as debug, not warning/exception."""

    def _raise_oserror(_uri, **_kwargs):
        raise ConnectionRefusedError("connect failed")

    debug_mock = MagicMock()
    warning_mock = MagicMock()

    monkeypatch.setattr(
        "custom_components.proflame_connect_wifi.client_base.connect",
        _raise_oserror,
    )
    monkeypatch.setattr(
        "custom_components.proflame_connect_wifi.client_base._LOGGER.debug",
        debug_mock,
    )
    monkeypatch.setattr(
        "custom_components.proflame_connect_wifi.client_base._LOGGER.warning",
        warning_mock,
    )

    assert await client_base.ProflameClientBase.test_connection("192.168.1.114", 88) is False
    debug_mock.assert_called_once()
    warning_mock.assert_not_called()
