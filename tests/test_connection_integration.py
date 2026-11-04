"""Exercise recovery over real loopback WebSockets with HA availability."""
import asyncio
import json
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from proflame_test_support import BASELINE_STATE
import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from custom_components.proflame_connect_wifi import client_base
from custom_components.proflame_connect_wifi.client import ProflameClient
from custom_components.proflame_connect_wifi.climate import ProflameClimate
from custom_components.proflame_connect_wifi.const import ApiControl
from custom_components.proflame_connect_wifi.coordinator import ProflameDataCoordinator
from custom_components.proflame_connect_wifi.entity import ProflameEntity
from custom_components.proflame_connect_wifi.util import Temperature

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("failure", ["close", "abort", "silence"])
async def test_real_socket_recovery_and_availability(tmp_path, failure):
    """Recover clean/reset/silent peers and publish availability without replay."""
    ready = asyncio.Event()
    disconnected = asyncio.Event()
    fail = asyncio.Event()
    second_open = asyncio.Event()
    acknowledge = asyncio.Event()
    ack_sent = asyncio.Event()
    send_state = asyncio.Event()
    command_received = asyncio.Event()
    sockets = []
    received = []

    async def fireplace(websocket):
        sockets.append(websocket)
        generation = len(sockets)
        assert await websocket.recv() == ApiControl.CONN_SYN
        if generation == 2:
            second_open.set()
            await acknowledge.wait()
        await websocket.send(ApiControl.CONN_ACK)
        if generation == 2:
            ack_sent.set()
            await send_state.wait()
        state = {**BASELINE_STATE, "main_mode": generation - 1}
        if generation == 1:
            state["fw_revision"] = "test-first-connection"
        await websocket.send(json.dumps(state))
        if generation == 1:
            await fail.wait()
            if failure == "close":
                await websocket.close()
            elif failure == "abort":
                websocket.transport.abort()
            else:
                await websocket.wait_closed()
            return
        async for message in websocket:
            if message == ApiControl.PING:
                await websocket.send(ApiControl.PONG)
            else:
                received.append(json.loads(message))
                command_received.set()

    async with serve(fireplace, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = ProflameClient("test-device", "127.0.0.1", port)
        hass = HomeAssistant(str(tmp_path))
        coordinator = ProflameDataCoordinator(hass, client, "Test fireplace")
        entity = ProflameEntity(coordinator, EntityDescription(key="main_mode"))
        notifications = []

        def on_update():
            notifications.append((entity.available, dict(coordinator.data)))

        def on_connection(connected):
            if connected:
                ready.set()
            else:
                disconnected.set()

        remove_listener = coordinator.async_add_listener(on_update)
        client.register_connection_callback(on_connection)
        try:
            with patch.object(client_base, "RECONNECT_MIN_DELAY", 0.01), patch.object(
                client_base, "KEEPALIVE_INTERVAL", 0.01
            ), patch.object(client_base, "KEEPALIVE_TIMEOUT", 0.15):
                assert not entity.available
                await client.open()
                await asyncio.wait_for(ready.wait(), 2)
                assert entity.available
                assert notifications[-1] == (
                    True,
                    {**BASELINE_STATE, "fw_revision": "test-first-connection"},
                )
                ready.clear()
                fail.set()
                await asyncio.wait_for(disconnected.wait(), 2)
                assert not entity.available
                assert notifications[-1][0] is False
                client.set_state("flame_control", 6)
                await asyncio.wait_for(second_open.wait(), 2)
                assert not entity.available
                acknowledge.set()
                await asyncio.wait_for(ack_sent.wait(), 2)
                # The peer's ack alone must never restore availability.
                await asyncio.sleep(0.02)
                assert not entity.available
                client.set_state("fan_control", 6)
                send_state.set()
                await asyncio.wait_for(ready.wait(), 2)
                assert entity.available
                assert coordinator.data == {**BASELINE_STATE, "main_mode": 1}
                assert client.get_state("fw_revision") is None
                assert notifications[-1] == (True, coordinator.data)
                coordinator.last_update_success = False
                assert not entity.available
                coordinator.last_update_success = True
                client.set_state("flame_control", 2)
                await asyncio.wait_for(command_received.wait(), 2)
                assert received == [{"flame_control": 2}]
                assert len(sockets) == 2
                await client.close()
                assert not entity.available
                assert notifications[-1][0] is False
        finally:
            acknowledge.set()
            send_state.set()
            fail.set()
            await client.close()
            remove_listener()


async def test_real_peer_without_ack_times_out():
    """An actual open WebSocket isn't sufficient for protocol readiness."""
    accepted = asyncio.Event()

    async def fireplace(websocket):
        assert await websocket.recv() == ApiControl.CONN_SYN
        accepted.set()
        await websocket.send(json.dumps(BASELINE_STATE))
        await websocket.wait_closed()

    async with serve(fireplace, "127.0.0.1", 0) as server:
        client = client_base.ProflameClientBase(
            "test",
            "127.0.0.1",
            server.sockets[0].getsockname()[1],
            auto_reconnect=False,
        )
        changes = []
        snapshots = []
        client.register_connection_callback(changes.append)
        client.register_callback(lambda *_: snapshots.append(client.full_state))
        try:
            with patch.object(client_base, "HANDSHAKE_TIMEOUT", 0.05):
                await client.open()
                await asyncio.wait_for(accepted.wait(), 2)
                await asyncio.wait_for(client._connection, 2)
            assert not client.connected
            assert changes == []
            assert snapshots == []
            assert client._ws is None
        finally:
            await client.close()


async def test_connection_probe_has_protocol_timeout():
    """Config-flow probing returns failure rather than hanging on a silent peer."""

    async def fireplace(websocket):
        assert await websocket.recv() == ApiControl.CONN_SYN
        await websocket.wait_closed()

    async with serve(fireplace, "127.0.0.1", 0) as server:
        with patch.object(client_base, "HANDSHAKE_TIMEOUT", 0.03):
            result = await asyncio.wait_for(
                client_base.ProflameClientBase.test_connection(
                    "127.0.0.1", server.sockets[0].getsockname()[1]
                ),
                1,
            )
        assert result is False


async def test_firmware_and_pongs_never_enable_controls(tmp_path):
    """Even ACK and steady pongs cannot replace the control-state baseline."""
    pongs = []

    async def fireplace(websocket):
        assert await websocket.recv() == ApiControl.CONN_SYN
        await websocket.send(ApiControl.CONN_ACK)
        await websocket.send('{"fw_revision": "test-metadata-only"}')
        try:
            while True:
                await websocket.send(ApiControl.PONG)
                pongs.append(True)
                await asyncio.sleep(0.005)
        except ConnectionClosed:
            pass

    async with serve(fireplace, "127.0.0.1", 0) as server:
        client = ProflameClient("test", "127.0.0.1", server.sockets[0].getsockname()[1])
        client._auto_reconnect = False
        coordinator = ProflameDataCoordinator(
            HomeAssistant(str(tmp_path)), client, "Test"
        )
        climate = ProflameClimate(coordinator)
        snapshots = []
        availability = []
        client.register_callback(lambda *_: snapshots.append(client.full_state))
        client.register_connection_callback(availability.append)
        try:
            with patch.object(client_base, "HANDSHAKE_TIMEOUT", 0.08):
                await client.open()
                await asyncio.wait_for(client._connection, 1)
            assert len(pongs) > 1
            assert not climate.available
            assert climate.hvac_action is None
            assert climate.hvac_mode is None
            assert (
                climate.temperature_unit
                == coordinator.hass.config.units.temperature_unit
            )
            assert client.temperature_unit is None
            await climate.async_set_temperature(temperature=72)
            assert client._queue.empty()
            assert snapshots == []
            assert availability == []
            assert coordinator.data == {}
        finally:
            await client.close()


async def test_partial_baseline_recovery_and_fahrenheit_commands(tmp_path):
    """Reconnect accumulates fresh state atomically and uses the new unit."""
    ready = asyncio.Event()
    drop = asyncio.Event()
    partial_sent = asyncio.Event()
    finish_baseline = asyncio.Event()
    received_commands = asyncio.Event()
    sockets = []
    commands = []
    fahrenheit = {
        **BASELINE_STATE,
        "main_mode": 2,
        "flame_control": 4,
        "temperature_unit": 1,
        "room_temperature": 700,
        "temperature_set": 720,
    }

    async def fireplace(websocket):
        sockets.append(websocket)
        assert await websocket.recv() == ApiControl.CONN_SYN
        await websocket.send(ApiControl.CONN_ACK)
        if len(sockets) == 1:
            await websocket.send(json.dumps(BASELINE_STATE))
            await drop.wait()
            await websocket.close()
            return
        # Split the replacement baseline into individual fields. An invalid unit
        # must not inherit the old connection's Celsius unit or enable controls.
        for key, value in fahrenheit.items():
            await websocket.send(
                json.dumps({key: 9 if key == "temperature_unit" else value})
            )
        partial_sent.set()
        await finish_baseline.wait()
        await websocket.send('{"temperature_unit": 1}')
        async for message in websocket:
            if message == ApiControl.PING:
                await websocket.send(ApiControl.PONG)
            else:
                commands.append(json.loads(message))
                if len(commands) == 2:
                    received_commands.set()

    async with serve(fireplace, "127.0.0.1", 0) as server:
        client = ProflameClient("test", "127.0.0.1", server.sockets[0].getsockname()[1])
        coordinator = ProflameDataCoordinator(
            HomeAssistant(str(tmp_path)), client, "Test"
        )
        climate = ProflameClimate(coordinator)
        snapshots = []
        notifications = []
        client.register_callback(lambda *_: snapshots.append(client.full_state))
        client.register_connection_callback(
            lambda connected: ready.set() if connected else None
        )
        remove_listener = coordinator.async_add_listener(
            lambda: notifications.append((climate.available, dict(coordinator.data)))
        )
        try:
            with patch.object(client_base, "RECONNECT_MIN_DELAY", 0.01):
                await client.open()
                await asyncio.wait_for(ready.wait(), 1)
                assert climate.available
                ready.clear()
                snapshots.clear()
                drop.set()
                await asyncio.wait_for(partial_sent.wait(), 1)
                async with asyncio.timeout(1):
                    while client.get_state("temperature_set") != 720:
                        await asyncio.sleep(0.001)
                assert not climate.available
                assert (
                    climate.temperature_unit
                    == coordinator.hass.config.units.temperature_unit
                )
                assert client.temperature_unit is None
                assert snapshots == []
                await climate.async_set_temperature(temperature=73)
                assert client._queue.empty()
                notifications.clear()
                finish_baseline.set()
                await asyncio.wait_for(ready.wait(), 1)
                assert climate.available
                assert snapshots and all(state == fahrenheit for state in snapshots)
                assert all(state == fahrenheit for _, state in notifications)
                assert notifications[-1] == (True, fahrenheit)
                assert client._stored_mode == 2
                assert client._stored_flame == 4
                assert climate.current_temperature == 70
                assert climate.target_temperature == 72
                await climate.async_set_temperature(temperature=73)
                client.set_target_temperature(Temperature.celcius(20))
                await asyncio.wait_for(received_commands.wait(), 1)
                assert commands == [{"temperature_set": 730}, {"temperature_set": 680}]
        finally:
            drop.set()
            finish_baseline.set()
            await client.close()
            remove_listener()
