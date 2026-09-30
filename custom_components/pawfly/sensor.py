"""``sensor.mode``: whether the light is manual, running a scene or running a program."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PawflyConfigEntry, PawflyCoordinator
from .entity import PawflyEntity
from .model import MODE_OPTIONS, mode_key

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PawflyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the mode sensor of one Pawfly device."""
    async_add_entities([PawflyModeSensor(entry.runtime_data)])


class PawflyModeSensor(PawflyEntity, SensorEntity):
    """The working mode reported in the status frame."""

    _attr_translation_key = "mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MODE_OPTIONS)
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PawflyCoordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def native_value(self) -> str | None:
        status = self.coordinator.data
        return None if status is None else mode_key(status)
