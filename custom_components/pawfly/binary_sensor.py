"""``binary_sensor.connected``: is the Bluetooth link to the light up."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PawflyConfigEntry, PawflyCoordinator
from .entity import PawflyEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PawflyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the connectivity sensor of one Pawfly device."""
    async_add_entities([PawflyConnectedSensor(entry.runtime_data)])


class PawflyConnectedSensor(PawflyEntity, BinarySensorEntity):
    """Link health. Always available: a disconnected light is exactly what it reports.

    With ``keep_connected`` off the link is released on purpose after idling, so ``off`` is
    routine there and only means "no session right now".
    """

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PawflyCoordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.link.ready

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Quantised link facts; each changes only on a real link event."""
        link = self.coordinator.link.snapshot()
        return {
            "link_state": link["state"],
            "hold": link["hold"],
            "drops_1h": link["drops_1h"],
            "last_drop": link["last_drop"],
            "reconnect_attempt": link["consecutive_failures"],
            "proxy": link["route_adapter"],
            "preferred_proxy": link["preferred_proxy"],
            "via_preferred_proxy": link["via_preferred_proxy"],
        }
