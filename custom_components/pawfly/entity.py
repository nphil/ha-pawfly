"""Base entity of the Pawfly Aquarium Light."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL, MODEL_ID
from .coordinator import PawflyCoordinator


class PawflyEntity(CoordinatorEntity[PawflyCoordinator]):
    """One entity of the light's device; state comes from the coordinator's pushed status."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PawflyCoordinator, key: str) -> None:
        super().__init__(coordinator)
        address = coordinator.address
        # Upper-case, colon-free: HA unique ids are case sensitive and proxies may report
        # either case, so one canonical form keeps entities from duplicating across reloads.
        self._attr_unique_id = f"{address.replace(':', '')}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            model_id=MODEL_ID,
            name=coordinator.config_entry.title,
        )

    @property
    def available(self) -> bool:
        """Available while the last status can be trusted (see ``PawflyCoordinator.available``)."""
        return self.coordinator.available
