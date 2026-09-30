"""Pure protocol tests (no Home Assistant).

Two independent sources of truth, both checked in:

* ``fixtures/vendor_golden.json`` is written by ``tools/vendor_golden``: the vendor app's own C# frame
  builders and receive path (copied verbatim from the decompiled app) run over a spread of inputs. Every
  encoder must produce the bytes the app would send, and the decoder must read replies the way the app does.
* ``fixtures/live_frames.json`` holds frames copied from the log of the real light (``docs/PROTOCOL.md``):
  what was sent for each command and what the light answered.

The rest are behaviour tests: ranges, error handling, damaged input, program assembly.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def _load_protocol():
    # By path: the module is pure Python and must stay importable without Home Assistant.
    spec = importlib.util.spec_from_file_location("pawfly_protocol_under_test", ROOT / "custom_components/pawfly/protocol.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


P = _load_protocol()
VENDOR = json.loads((FIXTURES / "vendor_golden.json").read_text(encoding="utf-8"))
LIVE = json.loads((FIXTURES / "live_frames.json").read_text(encoding="utf-8"))


def points_from(rows):
    return [P.Point(*row) for row in rows]


def reply(cmd: int, *payload: int) -> bytes:
    """A light-side frame built independently of protocol.py."""
    body = bytes((0xBD, len(payload) + 1, cmd, *payload))
    return body + bytes(((sum(body)) & 0xFF,))


# --------------------------------------------------------------------------------------
# Encoders against the vendor app's own code
# --------------------------------------------------------------------------------------


def test_checksum_matches_vendor_even_past_the_65535_wrap():
    for case in VENDOR["checksum"]:
        assert P.checksum(bytes.fromhex(case["data"])) == case["checksum"], case["data"][:24]


def test_keys_match_vendor():
    for case in VENDOR["keys"]:
        assert P.verify_key(case["key"]).hex() == case["verify"], case["key"]
        assert P.change_key(case["key"]).hex() == case["change"], case["key"]


def test_key_bytes_are_the_digit_pairs_in_reverse_order():
    rng = random.Random(7)
    for _ in range(50):
        key = "".join(rng.choice("0123456789") for _ in range(8))
        frame = P.verify_key(key)
        assert "".join(f"{b:02x}" for b in reversed(frame[3:7])) == key


@pytest.mark.parametrize(
    "key",
    ["", "1234567", "123456789", "1234567a", "１２３４５６７８", " 12345678", "12345678\n", "1234 678", 12345678, None],
)
def test_keys_must_be_exactly_eight_ascii_digits(key):
    with pytest.raises(ValueError):
        P.verify_key(key)
    with pytest.raises(ValueError):
        P.change_key(key)


def test_rename_matches_vendor_for_names_the_app_accepts():
    for case in VENDOR["rename"]:
        assert P.rename(case["name"]).hex() == case["hex"], case["name"]


def test_rename_takes_the_display_name_and_drops_a_typed_prefix():
    assert P.rename("PY4C-Tank") == P.rename("Tank")
    assert P.rename("PYLamp-4C_Tank") == P.rename("Tank")
    assert P.rename("PY4C  Tank")[3:-1] == b"  Tank"  # only "-" and "_" separators are dropped, like the app


def test_rename_length_and_character_rules():
    assert P.rename("0123456789ABCDEF")[1] == 17  # 16 characters fit, length byte counts the command too
    with pytest.raises(ValueError):
        P.rename("0123456789ABCDEFG")
    for bad in ("", "   ", "PY4C-", "PYLamp-4C_", "é", "tab\there", "new\nline", "日本"):
        with pytest.raises(ValueError):
            P.rename(bad)
    with pytest.raises(TypeError):
        P.rename(None)  # type: ignore[arg-type]


def test_display_name_matches_the_app():
    for case in VENDOR["display_name"]:
        assert P.display_name(case["advertised"]) == case["display"], case["advertised"]


def test_simple_frames_match_vendor():
    assert P.query_status().hex() == VENDOR["query_status"]
    for case in VENDOR["query_program"]:
        assert P.query_program(case["program"]).hex() == case["hex"]
    for case in VENDOR["program"]:
        assert P.program(case["program"]).hex() == case["hex"]
    for case in VENDOR["scenario"]:
        assert P.scenario(case["index"]).hex() == case["hex"]
    assert P.power(True).hex() == VENDOR["power"]["on"]
    assert P.power(False).hex() == VENDOR["power"]["off"]
    assert P.demo().hex() == VENDOR["demo"]


def test_level_frames_match_vendor_over_the_whole_range():
    for case in VENDOR["brightness"]:
        assert P.brightness(case["value"]).hex() == case["hex"], case["value"]
    for case in VENDOR["white"]:
        assert P.white(case["value"]).hex() == case["hex"], case["value"]
    for case in VENDOR["speed"]:
        assert P.speed(case["value"]).hex() == case["hex"], case["value"]
    for case in VENDOR["channel"]:
        assert P.channel(case["channel"], case["value"]).hex() == case["hex"], case
    # white is just channel 3
    assert all(P.white(v) == P.channel(3, v) for v in range(101))


def test_colour_frames_match_vendor_and_send_the_exact_percent_where_the_app_is_off_by_one():
    off_by_one = set()
    for case in VENDOR["color"]:
        r, g, b = case["red"], case["green"], case["blue"]
        ours = P.color(r, g, b)
        assert ours[3:6] == bytes((r, g, b))  # always the percent that was asked for
        if case["exact"]:
            assert ours.hex() == case["hex"], case
        else:
            off_by_one.update(v for v in (r, g, b) if v)
            assert ours.hex() != case["hex"]
    # the app's slider arithmetic truncates 0.53f * 100f and 0.59f * 100f to 52 and 58
    assert off_by_one == set(VENDOR["vendor_quirks"]["slider_percent_sent_off_by_one"]) == {53, 59}


def test_preview_frames_match_vendor():
    for case in VENDOR["preview"]:
        if case["exact"]:
            assert P.preview(tuple(case["rgbw"])).hex() == case["hex"], case
    assert P.preview(None).hex() == VENDOR["preview_end"]


def test_time_sync_matches_vendor_including_the_sunday_rule():
    for case in VENDOR["time_sync"]:
        assert P.time_sync(datetime.fromisoformat(case["iso"])).hex() == case["hex"], case["iso"]


def test_program_upload_matches_vendor():
    for case in VENDOR["program_upload"]:
        points = points_from([[p["hour"], p["minute"], p["red"], p["green"], p["blue"], p["white"]] for p in case["points"]])
        assert [f.hex() for f in P.program_upload(case["program"], points)] == case["frames"], case["name"]


def test_builtin_presets_match_what_the_app_draws():
    expected = {
        int(number): tuple(P.Point(p["hour"], p["minute"], p["red"], p["green"], p["blue"], p["white"]) for p in points)
        for number, points in VENDOR["presets"].items()
    }
    assert P.PRESETS == expected
    for points in P.PRESETS.values():
        times = [p.minute_of_day for p in points]
        assert times == sorted(set(times))  # sorted, no two points at one time
        assert len(points) <= P.MAX_PROGRAM_POINTS


# --------------------------------------------------------------------------------------
# Frames seen on the real light
# --------------------------------------------------------------------------------------


def _live_call(entry):
    fn, args = entry["fn"], entry["args"]
    if fn == "time_sync":
        return P.time_sync(datetime.fromisoformat(args[0]))
    if fn == "preview":
        return P.preview(None if args[0] is None else tuple(args[0]))
    return getattr(P, fn)(*args)


def test_every_command_sent_to_the_real_light_is_what_the_encoders_produce():
    for entry in LIVE["tx"]:
        assert _live_call(entry).hex() == entry["hex"], entry["label"]
    for key in ("upload", "upload_wrap"):
        upload = LIVE[key]
        frames = P.program_upload(upload["program"], points_from(upload["points"]))
        assert [f.hex() for f in frames] == upload["frames"], upload["label"]


def test_status_replies_of_the_real_light_decode_to_the_state_that_was_set():
    for entry in LIVE["status"]:
        expect = dict(entry["expect"])
        expect["mode"] = P.Mode(expect["mode"])
        assert P.parse(bytes.fromhex(entry["hex"])) == P.Status(**expect), entry["label"]


def test_key_reply_of_the_real_light():
    for entry in LIVE["key"]:
        assert P.parse(bytes.fromhex(entry["hex"])) == P.KeyResult(ok=entry["ok"])


def _assemble(frames):
    assembler = P.ProgramAssembler()
    done = []
    for frame in frames:
        for part in P.split_frames(frame):
            result = assembler.feed(P.parse(part))
            if result is not None:
                done.append(result)
    return done


def test_program_reads_of_the_real_light_are_reassembled_byte_exact():
    for entry in LIVE["programs"]:
        frames = [bytes.fromhex(h) for h in entry["frames"]]
        expected = [(entry["program"], sorted(points_from(entry["points"]), key=lambda p: p.minute_of_day))]
        assert _assemble(frames) == expected, entry["label"]
        # notifications may arrive in any order: the point numbers decide
        header, points = frames[0], frames[1:]
        random.Random(3).shuffle(points)
        assert _assemble([header, *points]) == expected, entry["label"]


# --------------------------------------------------------------------------------------
# Decoder against the vendor's receive path
# --------------------------------------------------------------------------------------


def _vendor_expectation(case):
    if case["kind"] == "key":
        return P.KeyResult(ok=case["ok"])
    if case["kind"] == "none":
        return None
    assert case["kind"] == "status", case
    mode = P.Mode(case["work_mode"]) if case["work_mode"] in (1, 2) else P.Mode.MANUAL
    scenario = case["scenario"] if mode is P.Mode.SCENARIO and 0 <= case["scenario"] < len(P.SCENARIO_NAMES) else None
    program = case["timer_id"] if mode is P.Mode.PROGRAM and 0 <= case["timer_id"] <= 5 else None
    return P.Status(
        power=case["power"],
        brightness=case["brightness"],
        speed=case["speed"],
        mode=mode,
        scenario=scenario,
        program=program,
        red=case["red"],
        green=case["green"],
        blue=case["blue"],
        white=case["white"],
    )


def test_replies_are_read_like_the_vendor_app_reads_them():
    for case in VENDOR["parse"]:
        assert P.parse(bytes.fromhex(case["hex"])) == _vendor_expectation(case), case["name"]


def test_program_replies_complete_like_the_vendor_app_completes_them():
    for sequence in VENDOR["parse_sequences"]:
        frames = [bytes.fromhex(h) for h in sequence["frames"]]
        expected = [
            (event["id"] - 125, points_from([[p["hour"], p["minute"], p["red"], p["green"], p["blue"], p["white"]] for p in event["points"]]))
            for event in sequence["events"]
        ]
        assert _assemble(frames) == expected, sequence["name"]


# --------------------------------------------------------------------------------------
# Behaviour: ranges, errors, damaged input, assembly
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fn", "args"),
    [
        ("brightness", (-1,)),
        ("brightness", (101,)),
        ("speed", (-1,)),
        ("speed", (256,)),
        ("white", (-1,)),
        ("white", (101,)),
        ("color", (101, 0, 0)),
        ("color", (0, -1, 0)),
        ("color", (0, 0, 101)),
        ("channel", (4, 10)),
        ("channel", (-1, 10)),
        ("channel", (0, 101)),
        ("scenario", (8,)),
        ("scenario", (-1,)),
        ("program", (6,)),
        ("program", (-1,)),
        ("query_program", (6,)),
        ("query_program", (-1,)),
        ("preview", ((101, 0, 0, 0),)),
        ("preview", ((0, 0, 0, -1),)),
    ],
)
def test_out_of_range_arguments_are_refused(fn, args):
    with pytest.raises(ValueError):
        getattr(P, fn)(*args)


@pytest.mark.parametrize(
    ("fn", "args"),
    [("brightness", (1.5,)), ("brightness", ("5",)), ("brightness", (True,)), ("color", (0, 0, None)), ("scenario", (None,)), ("channel", ("0", 5))],
)
def test_non_integer_arguments_are_refused(fn, args):
    with pytest.raises(TypeError):
        getattr(P, fn)(*args)


def test_brightness_zero_is_allowed_although_the_app_slider_starts_at_one():
    assert P.brightness(0)[3] == 0
    assert P.BRIGHTNESS_MIN == 1


def test_program_upload_sorts_points_and_numbers_them_from_zero():
    points = [P.Point(22, 0, 1, 2, 3, 4), P.Point(0, 0, 100, 100, 100, 100), P.Point(9, 45, 0, 100, 0, 0)]
    frames = P.program_upload(5, points)
    assert frames[0][3:5] == bytes((130, 3))  # DIY 3 travels as id 130, three points announced
    assert [(f[3], f[4], f[5]) for f in frames[1:]] == [(0, 0, 0), (1, 9, 45), (2, 22, 0)]


def test_program_upload_of_no_points_clears_the_program():
    frames = P.program_upload(3, [])
    assert len(frames) == 1
    assert frames[0][3:5] == bytes((128, 0))


def test_program_upload_accepts_the_apps_maximum_and_refuses_more():
    twelve = [P.Point(h * 2, 0, h, h, h, h) for h in range(12)]
    assert len(P.program_upload(4, twelve)) == 13
    with pytest.raises(ValueError):
        P.program_upload(4, [*twelve, P.Point(23, 59, 0, 0, 0, 0)])


@pytest.mark.parametrize(
    "program",
    [0, 1, 2, 6, -1],
)
def test_only_the_diy_slots_can_be_uploaded(program):
    with pytest.raises(ValueError):
        P.program_upload(program, [P.Point(1, 0, 0, 0, 0, 0)])


@pytest.mark.parametrize(
    "points",
    [
        [P.Point(1, 0, 0, 0, 0, 0), P.Point(1, 0, 5, 5, 5, 5)],  # two points at one time
        [P.Point(24, 0, 0, 0, 0, 0)],
        [P.Point(0, 60, 0, 0, 0, 0)],
        [P.Point(-1, 0, 0, 0, 0, 0)],
        [P.Point(1, 0, 101, 0, 0, 0)],
        [P.Point(1, 0, 0, 101, 0, 0)],
        [P.Point(1, 0, 0, 0, 101, 0)],
        [P.Point(1, 0, 0, 0, 0, 101)],
        [P.Point(1, 0, -1, 0, 0, 0)],
    ],
)
def test_invalid_program_points_are_refused(points):
    with pytest.raises(ValueError):
        P.program_upload(3, points)


def test_every_frame_is_well_formed():
    frames = [
        P.verify_key("00000000"),
        P.change_key("99999999"),
        P.rename("A"),
        P.rename("0123456789ABCDEF"),
        P.query_status(),
        *(P.query_program(i) for i in range(6)),
        P.power(True),
        P.brightness(100),
        P.speed(255),
        *(P.channel(c, 100) for c in range(4)),
        P.color(100, 100, 100),
        P.time_sync(datetime(2026, 1, 4, 23, 59, 59)),
        P.scenario(7),
        P.program(5),
        P.demo(),
        P.preview((100, 100, 100, 100)),
        P.preview(None),
        *P.program_upload(3, P.PRESETS[0]),
        *P.program_upload(4, [P.Point(0, 0, 0, 0, 0, 0)] * 0),
    ]
    for frame in frames:
        assert frame[0] in (P.FAMILY_SET, P.FAMILY_QUERY), frame.hex()
        assert frame[1] + 3 == len(frame), frame.hex()
        assert sum(frame[:-1]) & 0xFF == frame[-1], frame.hex()
    assert {f[0] for f in (P.verify_key("12345678"), P.query_status(), P.query_program(3))} == {P.FAMILY_QUERY}
    assert {f[0] for f in (P.power(True), P.color(1, 2, 3), P.demo())} == {P.FAMILY_SET}


def test_the_status_selectors_the_app_would_not_show_are_none():
    def status(mode, sel):
        return P.parse(reply(0x81, 1, 50, 0, mode, sel, 1, 2, 3, 4))

    assert status(1, 7).scenario == 7
    assert status(1, 8).scenario is None  # index 8 was accepted by the light but is not an effect
    assert status(2, 5 + 0).program == 5
    assert status(2, 130).program == 5
    assert status(2, 127).program is None
    assert status(2, 131).program is None
    unknown = status(3, 4)
    assert unknown.mode is P.Mode.MANUAL and unknown.scenario is None and unknown.program is None
    assert status(0, 7).scenario is None and status(0, 7).program is None  # stale selector in manual mode


def test_the_decoder_never_raises_and_refuses_damaged_frames():
    rng = random.Random(1234)
    for _ in range(3000):
        junk = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
        P.parse(junk)  # must simply not raise
        P.split_frames(junk)
    good = [bytes.fromhex(e["hex"]) for e in LIVE["status"][:6]] + [bytes.fromhex(e["hex"]) for e in LIVE["key"]]
    good += [bytes.fromhex(h) for e in LIVE["programs"][:2] for h in e["frames"]]
    for frame in good:
        assert P.parse(frame) is not None, frame.hex()
        for cut in range(len(frame)):
            assert P.parse(frame[:cut]) is None, (frame.hex(), cut)  # truncated
        for i in range(len(frame)):
            damaged = bytearray(frame)
            damaged[i] ^= 0x40
            assert P.parse(bytes(damaged)) is None, (frame.hex(), i)  # one wrong byte breaks the checksum


def test_frames_with_impossible_content_are_refused():
    assert P.parse(reply(0x83, 0, 24, 0, 1, 1, 1, 1)) is None  # hour 24: the app's TimeOnly would throw
    assert P.parse(reply(0x83, 0, 0, 60, 1, 1, 1, 1)) is None
    assert P.parse(reply(0x82, 0x83, 1, 0, 127, 0)) is None  # only DIY slots 0x80-0x82 exist
    assert P.parse(reply(0x82, 0x05, 1, 0, 127, 0)) is None
    assert P.parse(reply(0x90, 1, 2, 3, 4, 5, 6)) is None
    assert P.parse(bytes((0xAD, 6, 1, 1, 255, 255, 255, 255, 0xB1))) is None  # commands are never replies


def test_the_decoder_finds_a_frame_behind_leading_bytes_like_the_app():
    status = bytes.fromhex(LIVE["status"][0]["hex"])
    assert P.parse(b"\x00\x11" + status) == P.parse(status) is not None


def test_split_frames_separates_a_program_header_and_its_first_point():
    header = P.split_frames(bytes.fromhex(LIVE["programs"][0]["frames"][0]))[0]
    point = bytes.fromhex(LIVE["programs"][0]["frames"][1])
    assert P.split_frames(header + point) == [header, point]
    assert P.split_frames(header + point[:-3]) == [header]  # a truncated tail is dropped
    assert P.split_frames(b"") == []


def test_program_assembler_behaviour():
    p0 = P.ProgramPoint(0, P.Point(1, 0, 1, 2, 3, 4))
    p1 = P.ProgramPoint(1, P.Point(2, 0, 5, 6, 7, 8))
    assembler = P.ProgramAssembler()
    assert assembler.feed(p0) is None  # points before a header belong to nothing
    assert assembler.feed(P.ProgramHeader(3, 2)) is None
    assert assembler.feed(P.parse(bytes.fromhex(LIVE["status"][0]["hex"]))) is None  # unrelated replies are skipped
    assert assembler.feed(p1) is None
    assert assembler.feed(p0) == (3, [p0.point, p1.point])  # sorted by point number, not arrival
    assert assembler.feed(p0) is None  # nothing pending any more
    assert assembler.feed(P.ProgramHeader(4, 0)) == (4, [])  # an empty program is complete at once
    assembler.feed(P.ProgramHeader(3, 2))
    assembler.feed(p0)
    assert assembler.feed(P.ProgramHeader(5, 1)) is None  # a new header restarts the collection
    assert assembler.feed(p1) == (5, [p1.point])
    assert assembler.feed(None) is None


def test_scenario_and_program_tables_stay_in_step():
    assert len(P.SCENARIO_NAMES) == len(P.SCENARIO_COLORS) == P.SCENARIO_COUNT == 8
    assert len(set(P.SCENARIO_NAMES)) == 8
    assert len(set(P.PROGRAM_NAMES)) == 6
    assert all(0 <= v <= 100 for colour in P.SCENARIO_COLORS for v in colour)
