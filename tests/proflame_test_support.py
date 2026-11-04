"""Synthetic baseline and owner-provided observed protocol state for tests."""

BASELINE_STATE = {
    "main_mode": 0,
    "flame_control": 0,
    "fan_control": 0,
    "lamp_control": 0,
    "pilot_mode": 0,
    "temperature_unit": 0,
    "room_temperature": 210,
    "temperature_set": 220,
}

# Hardware snapshot supplied by James during reconnect review, 2026-11-04.
# These are the reported protocol fields, not a raw WebSocket frame capture.
OBSERVED_HARDWARE_STATE = {
    "main_mode": 0,
    "temperature_unit": 1,
    "lamp_control": 0,
    "pilot_mode": 0,
    "flame_control": 1,
    "fan_control": 1,
    "temperature_set": 690,
    "room_temperature": 720,
}
