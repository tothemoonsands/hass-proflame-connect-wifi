"""Tests for low-level websocket client behavior."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from websockets import ConnectionClosedError

from custom_components.proflame_connect_wifi.client_base import ProflameClientBase


def _closed_error() -> ConnectionClosedError:
    """Create a websocket closed exception for tests."""
    return ConnectionClosedError(None, None, None)


class _ClosedAsyncIterator:
    """Async iterator that raises websocket closure immediately."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise _closed_error()


@pytest.mark.asyncio
async def test_listener_exits_cleanly_when_connection_closes():
    """Listener should stop without logging exception noise on normal disconnects."""
    client = ProflameClientBase("device-id", "127.0.0.1")
    client._ws = _ClosedAsyncIterator()
    client._exception = MagicMock()

    await client._listener()

    client._exception.assert_not_called()


@pytest.mark.asyncio
async def test_keepalive_exits_cleanly_when_connection_closes(monkeypatch):
    """Keepalive should stop when websocket closes instead of logging exceptions."""
    client = ProflameClientBase("device-id", "127.0.0.1")
    client._send = AsyncMock(side_effect=_closed_error())
    client._exception = MagicMock()

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    await client._keepalive()

    client._exception.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_requeues_message_when_connection_closes():
    """Dispatcher should preserve in-flight message when websocket drops."""
    client = ProflameClientBase("device-id", "127.0.0.1")
    client._queue = asyncio.Queue()
    message = {"key": 1}
    client._queue.put_nowait(message)
    client._send = AsyncMock(side_effect=_closed_error())
    client._exception = MagicMock()

    await client._dispatcher()

    client._exception.assert_not_called()
    assert client._queue.qsize() == 1
    assert client._queue.get_nowait() == message
