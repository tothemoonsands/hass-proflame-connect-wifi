"""Provides switch control for Proflame fireplaces."""
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, PROFLAME_COORDINATOR, OperatingMode, Preset
from .coordinator import ProflameDataCoordinator
from .entity import ProflameEntity
from .util import coalesce


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create switches for Proflame fireplaces."""
    entry_id = config_entry.entry_id
    coordinator: ProflameDataCoordinator = hass.data[DOMAIN][entry_id][PROFLAME_COORDINATOR]
    entities = [
        ProflamePower(coordinator),
        ProflameSplitFlow(coordinator),
    ]
    async_add_entities(entities)


class ProflamePower(ProflameEntity, SwitchEntity):
    """Creates a device to control fireplace power."""

    def __init__(self, coordinator: ProflameDataCoordinator) -> None:
        """Create new instance of the ProflamePower class."""
        super().__init__(coordinator, SwitchEntityDescription(
            icon='mdi:fireplace',
            key='fireplace',
            translation_key='fireplace',
        ))
        self._attr_name = None

    @property
    def is_on(self) -> bool | None:
        """Return the current fireplace mode."""
        operating_mode = coalesce(self._device.operating_mode, OperatingMode.OFF)
        preset = self._device.preset
        if operating_mode == OperatingMode.OFF or preset == Preset.OFF:
            return False
        return True

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fireplace off."""
        self._device.turn_off()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the fireplace on."""
        self._device.turn_on()


class ProflameSplitFlow(ProflameEntity, SwitchEntity):
    """Creates a device to control split flow burner mode."""

    def __init__(self, coordinator: ProflameDataCoordinator) -> None:
        """Create new instance of the ProflameSplitFlow class."""
        super().__init__(coordinator, SwitchEntityDescription(
            icon='mdi:fire',
            key='split_flow',
            translation_key='split_flow',
        ))

    @property
    def is_on(self) -> bool | None:
        """Return whether split flow burner mode is enabled."""
        return self._device.split_flow

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn split flow burner mode off."""
        self._device.turn_off_split_flow()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn split flow burner mode on."""
        self._device.turn_on_split_flow()
