"""Constants for the Proflame integration."""
from enum import IntEnum, StrEnum

from .util import Temperature


class ApiAttrs(StrEnum):
    """Available attributes accessible via the API."""

    AUXILIARY = "auxiliary_out"
    BURNER_STATUS = "burner_status"
    CURRENT_TEMPERATURE = "room_temperature"
    FAN_SPEED = "fan_control"
    FIRMWARE_REVISION = "fw_revision"
    FLAME_HEIGHT = "flame_control"
    LIGHT_BRIGHTNESS = "lamp_control"
    OPERATING_MODE = "main_mode"
    PILOT_MODE = "pilot_mode"
    REMOTE_CONTROL = "remote_control"
    SPLIT_FLOW = "split_flow"
    TARGET_TEMPERATURE = "temperature_set"
    TEMPERATURE_UNIT = "temperature_unit"


class ApiControl(StrEnum):
    """Known control messages used by the API."""

    CONN_ACK = "PROFLAMECONNECTIONOPEN"
    CONN_SYN = "PROFLAMECONNECTION"
    PING = "PROFLAMEPING"
    PONG = "PROFLAMEPONG"


class OperatingMode(IntEnum):
    """Available operating modes for fireplace unit."""

    OFF = 0
    MANUAL = 1
    THERMOSTAT = 2
    SMART = 3


class PilotMode(IntEnum):
    """Available pilot modes for the fireplace."""

    INTERMITENT = 0
    CONTINUOUS = 1


class Preset(StrEnum):
    """Available presets for fireplace control."""

    OFF = "Off"
    MANUAL = "Manual"
    THERMOSTAT = "Thermostat"
    SMART = "Smart"


DOMAIN = "proflame_connect_wifi"

DEFAULT_DEVICE = 'Proflame Fireplace'
DEFAULT_NAME = 'Fireplace'
DEFAULT_PORT = 88

# Seconds allowed for the websocket handshake to complete.
CONNECT_TIMEOUT = 10
# Bounds for protocol startup, individual sends, and socket teardown.
HANDSHAKE_TIMEOUT = 10
SEND_TIMEOUT = 5
CLOSE_TIMEOUT = 5
# Seconds between PROFLAMEPING keepalive messages.
KEEPALIVE_INTERVAL = 5
# Seconds without any message from the fireplace before the connection is
# considered dead and reopened. The fireplace answers every ping within
# well under a second, so this tolerates several missed pongs.
KEEPALIVE_TIMEOUT = 30
# Reconnect backoff bounds in seconds.
RECONNECT_MIN_DELAY = 1
RECONNECT_MAX_DELAY = 60

PROFLAME_CLIENT = "client"
PROFLAME_COORDINATOR = "coordinator"

ADJUSTABLE_MODES = [
    OperatingMode.MANUAL,
    OperatingMode.THERMOSTAT,
]

MAX_FAN_SPEED = 6
MIN_FAN_SPEED = 0

MAX_FLAME_HEIGHT = 6
MIN_FLAME_HEIGHT = 0

MAX_LIGHT_BRIGHTNESS = 6
MIN_LIGHT_BRIGHTNESS = 0

MAX_TEMPERATURE: Temperature = Temperature.celcius(35)
MIN_TEMPERATURE: Temperature = Temperature.celcius(5)

# Every platform is enabled unconditionally. These fields form the required
# control baseline; split_flow is optional. None means an integer in tenths of a degree.
REQUIRED_STATE_RANGES = {
    ApiAttrs.OPERATING_MODE: (OperatingMode.OFF, OperatingMode.SMART),
    ApiAttrs.FLAME_HEIGHT: (MIN_FLAME_HEIGHT, MAX_FLAME_HEIGHT),
    ApiAttrs.FAN_SPEED: (MIN_FAN_SPEED, MAX_FAN_SPEED),
    ApiAttrs.LIGHT_BRIGHTNESS: (MIN_LIGHT_BRIGHTNESS, MAX_LIGHT_BRIGHTNESS),
    ApiAttrs.PILOT_MODE: (PilotMode.INTERMITENT, PilotMode.CONTINUOUS),
    ApiAttrs.TEMPERATURE_UNIT: (0, 1),
    ApiAttrs.CURRENT_TEMPERATURE: None,
    ApiAttrs.TARGET_TEMPERATURE: None,
}
