"""Tests for the ProflameClient."""
import pytest
from unittest.mock import MagicMock
from custom_components.proflame_connect_wifi.client import ProflameClient
from custom_components.proflame_connect_wifi.const import (
    ApiAttrs,
    MAX_FAN_SPEED,
    MIN_FAN_SPEED,
    OperatingMode
)

@pytest.fixture
def client():
    """Fixture to create a client instance."""
    client = ProflameClient("device_id", "127.0.0.1")
    # Mock the queue to verify set_state calls
    client._queue = MagicMock()
    # Populate some initial state
    client._state = {
        ApiAttrs.OPERATING_MODE: OperatingMode.MANUAL,
        ApiAttrs.FAN_SPEED: 0,
        ApiAttrs.FLAME_HEIGHT: 0,
        ApiAttrs.LIGHT_BRIGHTNESS: 0,
    }
    return client

def test_fan_speed_constraints(client):
    """Test fan speed is constrained."""
    client.set_fan_speed(100)
    # set_state puts {field: value} into queue.
    # put_nowait is a method of asyncio.Queue, we mocked it.
    client._queue.put_nowait.assert_called_with({ApiAttrs.FAN_SPEED: MAX_FAN_SPEED})

    client.set_fan_speed(-100)
    client._queue.put_nowait.assert_called_with({ApiAttrs.FAN_SPEED: MIN_FAN_SPEED})

def test_turn_off(client):
    """Test turn_off method."""
    client.turn_off()
    client._queue.put_nowait.assert_called_with({ApiAttrs.OPERATING_MODE: OperatingMode.OFF})

def test_tracking_state(client):
    """Test state tracking for restoring state."""
    # Simulate a state update from the device
    # By calling the callback directly or handle_json_message?
    # client.register_callback(self._track_state) is called in __init__

    # Let's call _track_state directly or simulate state change
    client._track_state(ApiAttrs.FAN_SPEED, 3)
    assert client._stored_fan_speed == 3

    client._track_state(ApiAttrs.FAN_SPEED, 0)
    assert client._stored_fan_speed == 3  # Should not update on 0


def test_split_flow_state_property(client):
    """Test split flow property mapping from raw state to bool."""
    client._state[ApiAttrs.SPLIT_FLOW] = 1
    assert client.split_flow is True

    client._state[ApiAttrs.SPLIT_FLOW] = 0
    assert client.split_flow is False

    client._state.pop(ApiAttrs.SPLIT_FLOW)
    assert client.split_flow is None


def test_split_flow_toggle(client):
    """Test split flow toggle methods write expected state."""
    client.turn_on_split_flow()
    client._queue.put_nowait.assert_called_with({ApiAttrs.SPLIT_FLOW: 1})

    client.turn_off_split_flow()
    client._queue.put_nowait.assert_called_with({ApiAttrs.SPLIT_FLOW: 0})
