"""``select.program``: manual, or one of the six stored programs."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PawflyConfigEntry, PawflyCoordinator
from .entity import PawflyEntity
from .model import OPTION_MANUAL, PROGRAM_OPTIONS, PROGRAM_SLUGS, program_option

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PawflyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the program select of one Pawfly device."""
    async_add_entities([PawflyProgramSelect(entry.runtime_data)])


class PawflyProgramSelect(PawflyEntity, SelectEntity):
    """Reflects the light's mode; choosing an option changes it.

    ``manual`` restores the last manual colour (the status frame keeps that register while a
    scene or program runs). While a *scene* runs none of the options fits, so the state is
    unknown rather than wrong. Changing the option never changes the power state.
    """

    _attr_translation_key = "program"
    _attr_options = list(PROGRAM_OPTIONS)

    def __init__(self, coordinator: PawflyCoordinator) -> None:
        super().__init__(coordinator, "program")

    @property
    def current_option(self) -> str | None:
        status = self.coordinator.data
        return None if status is None else program_option(status)

    async def async_select_option(self, option: str) -> None:
        if option == OPTION_MANUAL:
            await self.coordinator.async_activate_manual()
        else:
            await self.coordinator.async_activate_program(PROGRAM_SLUGS.index(option))
