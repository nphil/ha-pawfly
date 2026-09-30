"""Buttons: sync the clock, release the Bluetooth link."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_RESUME_AFTER
from .coordinator import PawflyConfigEntry, PawflyCoordinator
from .entity import PawflyEntity
from .release import async_release_entries

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class PawflyButtonDescription(ButtonEntityDescription):
    """A button and what pressing it does."""

    press_fn: Callable[[PawflyCoordinator], Awaitable[None]]
    always_available: bool = False


async def _release(coordinator: PawflyCoordinator) -> None:
    await async_release_entries(coordinator.hass, [coordinator.config_entry], DEFAULT_RESUME_AFTER)


BUTTONS = (
    PawflyButtonDescription(
        key="sync_time",
        translation_key="sync_time",
        # A setting-type action: disabled by default, the clock is synced on every reconnect
        # and daily anyway.
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        press_fn=lambda coordinator: coordinator.async_sync_time(),
    ),
    PawflyButtonDescription(
        key="release_link",
        translation_key="release_link",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=_release,
        # It has to stay usable exactly when the link is misbehaving.
        always_available=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PawflyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons of one Pawfly device."""
    async_add_entities(PawflyButton(entry.runtime_data, description) for description in BUTTONS)


class PawflyButton(PawflyEntity, ButtonEntity):
    """A button of the light's device."""

    entity_description: PawflyButtonDescription

    def __init__(self, coordinator: PawflyCoordinator, description: PawflyButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return True if self.entity_description.always_available else super().available

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)
