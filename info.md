The integration automatically reconnects after dropped or unresponsive fireplace
connections. Entities remain unavailable until the fireplace acknowledges the
connection and supplies valid control state. Commands issued while unavailable
and unsent commands from a lost connection are discarded.

Split-flow controls remain supported. See the README for connection timeouts and
required device state. Temperature units are imported from Home Assistant's
shared constants for compatibility with newer Home Assistant versions.
