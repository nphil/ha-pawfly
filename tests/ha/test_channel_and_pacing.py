"""Per-channel writes, the frame pacing the light's firmware needs, write kinds, upload retry."""

from __future__ import annotations

import asyncio

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.pawfly import link as link_module
from custom_components.pawfly import protocol
from custom_components.pawfly.const import DOMAIN

from .conftest import BASE, PRODUCTION_LINK_TIMING, wait_until
from .fake_light import TEST_ADDRESS


def _device_id(hass) -> str:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, TEST_ADDRESS), config_entry_id=entry.entry_id).id


async def _call(hass, service: str, **data):
    return await hass.services.async_call(DOMAIN, service, {"device_id": _device_id(hass), **data}, blocking=True, return_response=service == "get_program")


async def test_set_channel_writes_only_the_given_channels(hass, config_entry, light) -> None:
    """One frame per given channel, the others untouched, manual mode afterwards, power unchanged."""
    light.mode, light.selection, light.power = protocol.Mode.SCENARIO, 3, False
    light.push_status()
    light.clear()

    await _call(hass, "set_channel", blue=40, red=10, white=0)

    assert light.events_named("channel", "white", "color", "power", "scenario") == [
        ("channel", 0, 10),
        ("channel", 2, 40),
        ("white", 0, 3),
    ]
    await wait_until(lambda: hass.states.get(f"sensor.{BASE}_mode").state == "manual", message="manual mode")
    assert light.rgbw == [10, 100, 40, 0]  # green (the register's 100) was never touched
    assert hass.states.get(f"light.{BASE}").state == "off"


@pytest.mark.parametrize(
    "data",
    [{}, {"red": 101}, {"green": -1}, {"blue": True}, {"white": "lots"}, {"red": 10.5}],
)
async def test_set_channel_rejects_bad_input_without_writing(hass, config_entry, light, data) -> None:
    light.clear()
    with pytest.raises(vol.Invalid):
        await _call(hass, "set_channel", **data)
    assert not light.frames


async def test_frames_are_paced_by_the_gap_the_firmware_needs(hass, config_entry, light, monkeypatch) -> None:
    """The light drops frames that arrive faster than about 20 ms without any error, so every
    write waits; production must stay at 60 ms or more."""
    assert PRODUCTION_LINK_TIMING["FRAME_GAP"] >= 0.06
    monkeypatch.setattr(link_module, "FRAME_GAP", 0.05)
    light.clear()

    points = [{"time": f"{hour:02d}:00", "red": hour * 4, "green": 0, "blue": 0, "white": 0} for hour in range(1, 13)]
    await _call(hass, "set_program", program="diy_1", points=points)

    assert len(light.frames) >= 13  # header + 12 points (+ the read-back query)
    gaps = [later - earlier for earlier, later in zip(light.frame_times, light.frame_times[1:], strict=False)]
    assert min(gaps) >= 0.045


@pytest.mark.parametrize(
    ("properties", "expected_response"),
    [
        (("read", "write-without-response", "write", "notify"), False),
        (("write-without-response", "notify"), False),
        (("read", "write", "notify"), True),
    ],
)
async def test_write_kind_prefers_unacknowledged_when_supported(
    hass, setup_entry, light, properties, expected_response
) -> None:
    """Unacknowledged writes are the verified path on the real light; acknowledged only as fallback."""
    light.char.properties = list(properties)

    await setup_entry()
    await _call(hass, "sync_time")

    assert light.write_responses
    assert set(light.write_responses) == {expected_response}


async def test_lost_upload_frame_is_repaired_by_one_retry(hass, config_entry, light, monkeypatch) -> None:
    """A dropped frame is only visible in the read-back; the upload is repeated once."""
    real_finish = light._finish_upload
    uploads: list[int] = []

    def _first_upload_loses_a_point() -> None:
        uploads.append(1)
        if len(uploads) == 1:
            light._upload["points"] = light._upload["points"][:1]
        real_finish()

    monkeypatch.setattr(light, "_finish_upload", _first_upload_loses_a_point)
    light.clear()
    points = [
        {"time": "07:00", "red": 0, "green": 0, "blue": 0, "white": 0},
        {"time": "08:00", "red": 50, "green": 50, "blue": 50, "white": 50},
    ]

    response = await _call(hass, "set_program", program="diy_2", points=points)

    assert len(light.events_named("program_header")) == 2  # uploaded twice
    assert [point.hour for point in light.programs[4]] == [7, 8]
    assert response is None  # set_program is called without return_response here


async def test_upload_that_never_sticks_fails_after_the_single_retry(hass, config_entry, light) -> None:
    light.store_programs = False
    light.clear()
    with pytest.raises(HomeAssistantError) as raised:
        await _call(hass, "set_program", program="diy_3", points=[{"time": "07:00"}])
    assert raised.value.translation_key == "program_readback_mismatch"
    assert len(light.events_named("program_header")) == 2
    await asyncio.sleep(0)
