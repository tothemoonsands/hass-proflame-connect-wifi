"""Real HA state-writing regressions for unavailable climate entities."""
import asyncio
import json
import logging
from unittest.mock import patch

from homeassistant.const import STATE_UNAVAILABLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity, entity_registry
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from proflame_test_support import OBSERVED_HARDWARE_STATE
import pytest
from websockets.asyncio.server import serve

from custom_components.proflame_connect_wifi.client import ProflameClient
from custom_components.proflame_connect_wifi.climate import ProflameClimate
from custom_components.proflame_connect_wifi.const import ApiControl
from custom_components.proflame_connect_wifi.coordinator import ProflameDataCoordinator


@pytest.mark.asyncio
@pytest.mark.parametrize("units", [METRIC_SYSTEM, US_CUSTOMARY_SYSTEM])
async def test_unavailable_state_writes_use_presentation_unit(tmp_path, units):
    """HA must publish unavailable both before readiness and after a bad unit."""
    send_baseline = asyncio.Event()
    invalidate_unit = asyncio.Event()
    ready = asyncio.Event()

    async def fireplace(websocket):
        assert await websocket.recv() == ApiControl.CONN_SYN
        await websocket.send(ApiControl.CONN_ACK)
        await send_baseline.wait()
        await websocket.send(json.dumps(OBSERVED_HARDWARE_STATE))
        await invalidate_unit.wait()
        await websocket.send('{"temperature_unit": 9}')
        await websocket.wait_closed()

    hass = HomeAssistant(str(tmp_path))
    hass.config.units = units
    entity.async_setup(hass)
    await device_registry.async_load(hass)
    await entity_registry.async_load(hass)
    component = EntityComponent(logging.getLogger(__name__), "climate", hass)
    async with serve(fireplace, "127.0.0.1", 0) as server:
        client = ProflameClient(
            "observed-device", "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        client._auto_reconnect = False
        coordinator = ProflameDataCoordinator(hass, client, "Fireplace")
        climate = ProflameClimate(coordinator)
        climate.entity_id = "climate.test_fireplace"
        client.register_connection_callback(
            lambda connected: ready.set() if connected else None
        )
        try:
            # EntityComponent installs the actual CoordinatorEntity listener and
            # exercises HA capability conversion, rather than mocking state writes.
            await component.async_add_entities([climate])
            climate.async_write_ha_state()
            state = hass.states.get(climate.entity_id)
            assert state.state == STATE_UNAVAILABLE
            assert climate.temperature_unit == units.temperature_unit
            assert client.temperature_unit is None
            expected_limits = (5, 35) if units is METRIC_SYSTEM else (41, 95)
            assert (
                state.attributes["min_temp"],
                state.attributes["max_temp"],
            ) == expected_limits
            with patch.object(client, "set_target_temperature") as encode:
                await climate.async_set_temperature(temperature=72)
                encode.assert_not_called()
            assert client._queue.empty()

            await client.open()
            send_baseline.set()
            await asyncio.wait_for(ready.wait(), 2)
            climate.async_write_ha_state()
            state = hass.states.get(climate.entity_id)
            assert state.state == "off"
            assert client.full_state == OBSERVED_HARDWARE_STATE
            assert climate.temperature_unit == UnitOfTemperature.FAHRENHEIT
            expected_current = 22.2 if units is METRIC_SYSTEM else 72
            assert state.attributes["current_temperature"] == pytest.approx(
                expected_current
            )
            assert (
                state.attributes["min_temp"],
                state.attributes["max_temp"],
            ) == expected_limits

            invalidate_unit.set()
            await asyncio.wait_for(client._connection, 2)
            # The coordinator's disconnect callback must already have published
            # unavailable, not left the previous off state behind after an error.
            assert hass.states.get(climate.entity_id).state == STATE_UNAVAILABLE
            climate.async_write_ha_state()
            state = hass.states.get(climate.entity_id)
            assert state.state == STATE_UNAVAILABLE
            assert "current_temperature" not in state.attributes
            assert climate.temperature_unit == UnitOfTemperature.FAHRENHEIT
            assert client.temperature_unit is None
            assert (
                state.attributes["min_temp"],
                state.attributes["max_temp"],
            ) == expected_limits
            with patch.object(client, "set_target_temperature") as encode:
                await climate.async_set_temperature(temperature=72)
                encode.assert_not_called()
            assert client._queue.empty()
        finally:
            send_baseline.set()
            invalidate_unit.set()
            await client.close()
            await component.async_remove_entity(climate.entity_id)
            await hass.async_stop()
