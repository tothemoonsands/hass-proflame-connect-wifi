# Integration Blueprint

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

![Project Maintenance][maintenance-shield]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

[![Discord][discord-shield]][discord]
[![Community Forum][forum-shield]][forum]

_Integration to integrate with [integration_blueprint][integration_blueprint]._

**This integration will set up the following platforms.**

Platform | Description
-- | --
`binary_sensor` | Show something `True` or `False`.
`sensor` | Show info from blueprint API.
`switch` | Switch something `True` or `False`.

## Installation

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
1. If you do not have a `custom_components` directory (folder) there, you need to create it.
1. In the `custom_components` directory (folder) create a new folder called `proflame_connect_wifi`.
1. Download _all_ the files from the `custom_components/proflame_connect_wifi/` directory (folder) in this repository.
1. Place the files you downloaded in the new directory (folder) you created.
1. Restart Home Assistant
1. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Proflame"

## Configuration is done in the UI

<!---->

## Home Assistant compatibility

Temperature units use Home Assistant's shared constants, so startup does not
depend on the climate component re-exporting `UnitOfTemperature`.

## Connection recovery

The integration reconnects automatically after a WebSocket close, network error,
or 30 seconds without incoming traffic. Retries back off from 1 to 60 seconds;
a successful Proflame handshake and fresh state reset the backoff. WebSocket
opening and protocol startup each have a 10-second timeout; sends and graceful
socket teardown each have a 5-second timeout.

Entities remain unavailable until the fireplace acknowledges the protocol and
reports a complete, valid control-state baseline on the current connection.
Partial JSON messages accumulate within the 10-second protocol-startup deadline;
firmware metadata and pongs do not satisfy or extend that deadline. State
callbacks are published only after the entire baseline and acknowledgement have
arrived, so consumers never receive a partially initialized baseline.

The platforms are created unconditionally. Their required baseline is:

| Protocol field | Required value | Used by |
| --- | --- | --- |
| `main_mode` | Integer 0–3 | Climate mode/preset, power, fan, flame |
| `flame_control` | Integer 0–6 | Flame level, climate preset, restored flame |
| `fan_control` | Integer 0–6 | Fan level and restoration |
| `lamp_control` | Integer 0–6 | Light brightness and restoration |
| `pilot_mode` | Integer 0–1 | Pilot selector |
| `temperature_unit` | Integer 0 (Celsius) or 1 (Fahrenheit) | Temperature display and command encoding |
| `room_temperature` | Integer, tenths of the reported unit | Climate current temperature |
| `temperature_set` | Integer 50–350 (Celsius) or 410–950 (Fahrenheit) | Climate target temperature |

Booleans, numeric strings, floats, nulls, and out-of-range control values do not
qualify. Invalid startup fields must be replaced by valid values before the
deadline. Invalid required state received after readiness disconnects the client
and starts recovery. Missing protocol temperature units are never assumed to be
Celsius when encoding commands.

Home Assistant needs a valid climate presentation unit even while unavailable
to convert capability attributes such as minimum and maximum temperature. Before
the first ready baseline, the entity uses HA's configured temperature unit; after
readiness, it retains the last valid device unit across disconnects. This display
fallback never enables commands: temperature commands require availability and
use only the client's validated protocol unit.

These requirements follow this integration's existing enums, control limits,
and entity dependencies. Tests use synthetic messages plus the hardware snapshot
reported during review on 2026-11-04: mode 0, unit 1, lamp 0, pilot 0, flame 1,
fan 1, setpoint 690, and room temperature 720. That snapshot passes readiness and
HA state-writing regression tests; it is recorded as reported fields, not a raw
WebSocket capture. There is no capability negotiation: firmware that omits a required field
(even when a particular HA entity is disabled), uses different ranges or units,
or reports an unset target such as zero will remain unavailable and retry. Target
limits match the integration's supported 5–35 °C / 41–95 °F setpoints even while
off. No undocumented range is imposed on the room-temperature sensor beyond
requiring an integer. `fw_revision`, `burner_status`, `remote_control`,
`auxiliary_out`, and `split_flow` aren't required for readiness. The optional
split-flow control remains supported when the device reports it. Readiness does not establish actual burner activity; the existing
climate heating indication is inferred from mode/preset, not `burner_status`.

Entities become unavailable when connection loss is detected,
before socket cleanup completes. Commands issued while unavailable and unsent
queued commands are dropped rather than replayed after reconnection. Already
transmitted commands cannot be recalled or confirmed by this recovery mechanism.

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

***

[proflame_connect_wifi]: https://github.com/Pharrox/hass-proflame-connect-wifi
[buymecoffee]: https://www.buymeacoffee.com/pharrox
[buymecoffeebadge]: https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=for-the-badge
[commits-shield]: https://img.shields.io/github/commit-activity/y/Pharrox/hass-proflame-connect-wifi.svg?style=for-the-badge
[commits]: https://github.com/Pharrox/hass-proflame-connect-wifi/commits/main
[discord]: https://discord.gg/Qa5fW2R
[discord-shield]: https://img.shields.io/discord/330944238910963714.svg?style=for-the-badge
[exampleimg]: example.png
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/Pharrox/hass-proflame-connect-wifi.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-Kevin%20Lucas%20%40Pharrox-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/Pharrox/hass-proflame-connect-wifi.svg?style=for-the-badge
[releases]: https://github.com/Pharrox/hass-proflame-connect-wifi/releases
