"""The light: on/off, master brightness, RGBW mix and the built-in scenes as effects."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol
from .coordinator import PawflyConfigEntry, PawflyCoordinator
from .entity import PawflyEntity
from .model import (
    EFFECT_LIST,
    PROGRAM_SLUGS,
    brightness_to_device,
    brightness_to_ha,
    channel_to_device,
    channel_to_ha,
    display_rgbw,
    is_diy,
    mode_key,
    program_name,
    scenario_name,
)

# Commands go through the coordinator's serialised link; nothing to throttle per entity.
PARALLEL_UPDATES = 1

#: Constant facts a dashboard card needs (so it hardcodes nothing and needs no extra websocket
#: command). HA stores identical attribute sets once, so they cost the recorder nothing.
STATIC_ATTRIBUTES: dict[str, Any] = {
    "programs": [
        {"program": index, "slug": slug, "name": protocol.PROGRAM_NAMES[index], "editable": is_diy(index)}
        for index, slug in enumerate(PROGRAM_SLUGS)
    ],
    "scene_colors": {
        name: list(colors) for name, colors in zip(protocol.SCENARIO_NAMES, protocol.SCENARIO_COLORS, strict=True)
    },
    "max_program_points": protocol.MAX_PROGRAM_POINTS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PawflyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the light of one Pawfly device."""
    async_add_entities([PawflyLight(entry.runtime_data)])


class PawflyLight(PawflyEntity, LightEntity):
    """The main light; it carries the device's name (``has_entity_name`` with no own name)."""

    _attr_name = None
    _attr_color_mode = ColorMode.RGBW
    _attr_supported_color_modes = {ColorMode.RGBW}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = EFFECT_LIST

    def __init__(self, coordinator: PawflyCoordinator) -> None:
        super().__init__(coordinator, "light")

    @property
    def _status(self) -> protocol.Status | None:
        return self.coordinator.data

    @property
    def is_on(self) -> bool | None:
        status = self._status
        return None if status is None else status.power

    @property
    def brightness(self) -> int | None:
        """Master brightness, HA scale (the device's 1-100 % slider)."""
        status = self._status
        return None if status is None else brightness_to_ha(status.brightness)

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """The colour mix at full brightness (0-255 per channel; the device keeps 0-100 %)."""
        status = self._status
        if status is None:
            return None
        red, green, blue, white = (channel_to_ha(value) for value in display_rgbw(status))
        return (red, green, blue, white)

    @property
    def effect(self) -> str | None:
        """The running scene's name; ``None`` in manual or program mode."""
        status = self._status
        if status is not None and status.mode is protocol.Mode.SCENARIO:
            return scenario_name(status.scenario)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """What the light is doing beyond colour: manual, a scene or a stored program."""
        status = self._status
        if status is None:
            return None
        return {
            **STATIC_ATTRIBUTES,
            "mode": mode_key(status),
            "program": status.program,
            "program_name": program_name(status.program),
            "scenario": status.scenario,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Power on and apply brightness / an effect / a colour (manual mode)."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        rgbw = kwargs.get(ATTR_RGBW_COLOR)
        effect = kwargs.get(ATTR_EFFECT)
        await self.coordinator.async_turn_on(
            brightness=None if brightness is None else brightness_to_device(int(brightness)),
            rgbw=None if rgbw is None else tuple(channel_to_device(int(value)) for value in rgbw),  # type: ignore[arg-type]
            effect=effect,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_turn_off()
