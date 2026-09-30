# Pawfly / PinYing PY4C — wire protocol

Source of truth for `custom_components/pawfly/protocol.py`. Derived from the decompiled vendor app (`research/decompiled`;
citations in [APP-FEATURES.md](APP-FEATURES.md)) and checked against the real light (`PY4C-BT-PSTL`, `78:9C:E7:2D:FB:A3`) on
2026-09-30. **✔ live** marks behaviour observed on that light; the complete log of every command and reply is the appendix at
the end of this file. Tests: `tests/test_protocol.py` (frames checked against the vendor's own C# code and against the frames
copied from this log).

## Transport

- One GATT service `0000ffe0-0000-1000-8000-00805f9b34fb` with one characteristic `0000ffe1-…` for writing and notifying (the
  usual transparent-UART BLE module). The characteristic offers write-without-response; all live writes used that. ✔ live
- Subscribe to notifications first, then send the key. After every **new** connection the light ignores every query until the key
  was verified (queries after a fresh link were silent until the key went in). ✔ live
- The controller is behind a UART: frames that arrive too fast are dropped silently (see *Pacing*).

## Frame layout

```
[family, length, command, payload..., checksum]
```

- `family`: `0xAD` = commands that change something, `0xBD` = queries and authentication. Every reply starts with `0xBD`.
- `length` = `len(payload) + 1` (command byte + payload); the whole frame is `length + 3` bytes.
- `checksum` = running sum of all previous bytes (the vendor reduces `% 65535` at every step; identical for real frames), low byte.
  Replies carry a correct checksum. ✔ live
- Unused payload bytes are `0xFF`.
- **`0xAD` commands are never acknowledged**: no echo, no error. Confirm with a status query. ✔ live (758 commands, none answered)

## Commands

| Purpose | Frame | Notes |
|---|---|---|
| Verify key | `BD 06 0A k4 k3 k2 k1 FF cs` | key = 8 digits `k0..k7` sent as `k6k7 k4k5 k2k3 k0k1` (`12345678` → `78 56 34 12`). Reply `BD 06 8A 01 FF FF FF FF cs` = accepted ✔. Only answered on a fresh link; a repeated verify on an authenticated link gets no reply ✔. Refused = `8A 00` per the app code, **not tried** (possible lock-out). Default key is `12345678` ✔. |
| Change key | `AD 06 20 k4 k3 k2 k1 FF cs` | **encoder only, never sent to the light** |
| Rename | `AD n+1 21 <name, n bytes> cs` | the app sends the display name without the `PY4C` prefix, ≤ 16 characters. **Encoder only, never sent to the light.** |
| Query status | `BD 06 01 FF FF FF FF FF cs` | ✔ |
| Query DIY program | `BD 06 02 id FF FF FF FF cs` | `id` 128/129/130 = DIY 1/2/3 ✔. The light only looks at bit 7 and bits 0-1 of `id` (132 = 128, 133 = 129, 134 = 130, 136…192 with low bits 0 = 128); low bits 3 and every id below 128 (presets 0-2 included, 3 tries, plus 3-7 and 127) get **no reply** ✔. So there are exactly three DIY slots. |
| Power | `AD 06 01 on FF FF FF FF cs` | 1 on, 0 off ✔. While off the light still answers queries and accepts every other write. |
| Brightness | `AD 06 02 pct FF FF FF FF cs` | app slider 1-100; **0 is accepted** and is fully dark while "on" ✔ (camera). Never changes the mode; also scales scenarios and programs ✔. |
| Speed | `AD 06 03 v FF FF FF FF cs` | **no-op** ✔: accepted, never stored (`status.speed` stayed 0 for 1, 2, 3, 4, 5, 8, 9, 10, 11, 15, 20, 50, 100, 101, 127, 128, 200, 255), RGB-cycle animation unchanged (camera, speeds 0, 1, 255). The app never sends it. |
| Channel | `AD 06 04 v chl FF FF FF cs` | `chl` 0 red, 1 green, 2 blue, 3 white ✔. The app only uses 3 (white); 0-2 are hidden. Sets one channel, leaves the others, flips to manual mode ✔ (status + camera). |
| Colour | `AD 06 05 R G B FF FF cs` | 0-100 each; flips to manual mode ✔ (from scenario and from program). |
| Time sync | `AD 06 06 hh mm ss dow FF cs` | local time, dow Monday 1 … Sunday 7 ✔. The light's clock followed a synced time to within the camera's 1-2 s (see *Fade*). |
| Scenario | `AD 06 07 idx FF FF FF FF cs` | 0-7 ✔ (mode 1, `sel` = idx). Index 8 is accepted (mode 1, `sel` 8) but the light goes dark: no ninth effect ✔. |
| Program select | `AD 06 08 id FF FF FF FF cs` | `id` 0/1/2 built-in styles, 128/129/130 DIY 1-3 ✔ (mode 2). An empty DIY slot can be selected: the light is dark ✔. |
| DIY header | `AD 06 09 id count 00 7F FF cs` | starts an upload. The last three bytes are constants: changing them (`00 7F FF` → `00 00 FF`, `7B`, `04`, `01 7F FF`, `00 7F 00`) changed nothing, no weekday mask or similar ✔. |
| DIY point | `AD 08 0A ord hh mm R G B W cs` | one per point, sorted by time, `ord` from 0. 12 points verified; more untested (the app's limit is 12). |
| Demo | `AD 06 0B FF FF FF FF FF cs` | only does something while a program runs ✔ (no visible effect in manual mode). Replays the running program's 24 hours at **1 hour per second, about 24 s**, starting at 00:00, then the light returns to normal ✔ (camera, dim DIY program). Status stays `mode 2` throughout. No stop command; a colour command ended it within 2 s ✔. |
| Preview | `AD 06 0C 01 R G B W cs` / `AD 06 0C 00 00 00 00 00 cs` | shows the colour at once over **any** mode (manual, scenario, program) and changes neither mode nor status ✔ (camera); the second frame ends it and the light returns to what it was doing ✔. Brightness applies. |

## Replies

```
status : BD 0A 81 power bright speed mode sel R G B W cs           13 bytes
key    : BD 06 8A ok FF FF FF FF cs                                 9 bytes
header : BD 06 82 id count 00 7F x cs                               9 bytes, x = stale buffer byte, ignore
point  : BD 08 83 ord hh mm R G B W cs                              11 bytes
```

- `mode`: 0 manual, 1 scenario (`sel` = 0-7), 2 program (`sel` = 0-2, or 128-130 for DIY 1-3). `sel` keeps its last value in manual
  mode (stale).
- `R G B W` (0-100) are the **manual colour register**. Scenarios and programs never change it, so the status never shows what the
  light is really showing ✔ (checked in all 8 scenarios and in programs). Power off keeps everything.
- A program query is answered by one header, then `count` point frames that trickle in over about 0.35 s for 12 points; the header
  comes about 100 ms after the query. BlueShark's `send_raw` only returns the first notification; the rest were collected with its
  `listen` service started right afterwards. Empty program: header with count 0 and nothing else. ✔
- Header `id` echoes the slot (`80`/`81`/`82`), whatever alias was queried.

## Behaviour verified on the light

| Area | Result |
|---|---|
| Mode switching | colour and white/channel writes → manual; scenario select → scenario; program select → program; brightness, preview, time sync never change the mode ✔ |
| Writes while power is off | colour, white, brightness, scenario are accepted and stored, the light stays off, `power(True)` then shows them ✔ |
| Scenarios (camera) | 0 Cloudy steady medium, 1 Thunderstorm animated (alternates bright and dim), 2 Sunny steady bright, 3 Moonlight steady dim blue, 4/5/6 steady bright, 7 RGB Cycle animated ✔ |
| Empty DIY slot | selectable, light dark ✔ |
| Single-point program | holds its colour all day (blue shown at 01:5x for a point at 12:00) ✔ |
| Brightness in a program | applies (blue constant program: brightness 100/50/10 → camera 0.713/0.609/0.512, off 0.474) ✔ |
| Time sync | light's clock follows it; built-in Japanese style was dark at 01:1x with the synced clock ✔ |

### Fade between program points (not visible in the status, measured by camera)

The status never reflects the output, so the fade was measured with the plant-room camera (tank brightness relative to a fixed
reference, method below) while the light's clock was set with `time_sync` to 23:58:20 and DIY 1 held
`00:01 green`, `00:03 dark`, `23:59 dark`. Camera ratio 0.474 = dark, 0.965 = green:

- until 23:59:00: dark (previous point 00:03 dark → 23:59 dark);
- **23:59:00 → 00:01:00: smooth linear ramp 0.475 → 0.965**: the last point of the day fades into the first point of the next day,
  across midnight, with no step;
- **00:01:00 → 00:03:00: smooth linear ramp back 0.965 → 0.474**;
- dark afterwards.

So: linear per-channel fade between neighbouring points, wrapping across midnight; both ends land on the point times within the
camera's 1-2 s resolution. This is exactly the model of the card's `curve.ts`.

### Built-in styles versus the app's tables

`PRESETS` are the app's tables (`Lamp4CTimer.cs:107-149`); the light does not answer a query for them. To check them, the clock was
set to chosen times, the style selected, and the camera brightness compared with the same interpolated colour set by hand:

- Japanese 07:07 and 18:07, Jungle 08:15 and 18:30: match (difference ≤ 0.005 of the ratio).
- Dutch 06:00, 08:00, 11:00, 12:00, 13:00, 14:30, 16:00, 16:30, 17:00, 17:30, 18:00, 19:00, 20:30: match (≤ 0.006).
- **Dutch 09:00-10:00 does not**: the light is much dimmer than the app's table (at 09:00 luma 162.7 versus 179.5 for the table's
  `65 30 20 50`). At 10:00 the light shows about the table's 09:00 colour, and 09:15/09:30 fit "the `65 30 20 50` point sits near
  10:00" (luma 159.1 vs 157.4 and 176.1 vs 175.4), so the firmware's Dutch curve differs from the app's table between 08:00 and 11:00.
  The exact firmware values could not be recovered with a scalar brightness measurement. The integration keeps the app's table
  (that is what the vendor app shows).

## Pacing (write-without-response through an ESPHome proxy)

12-point DIY uploads (header + 12 point frames) with a fixed gap, read back and compared byte for byte:

| Gap between frames | Result |
|---|---|
| ~1 ms (back to back) | 4 of 12 points lost (old content stayed) |
| 10 ms | 1 point lost |
| 15 ms | 2 of 4 uploads exact (1-3 points lost otherwise) |
| 20 ms | 4 / 4 exact |
| 30 ms | 4 / 4 exact |
| 40 ms | 4 / 4 exact |
| 80 ms | 4 / 4 exact (+ 100 ms, 1 run) |

The light gives no error for a dropped frame: **pace every frame ≥ 80 ms** (`protocol.FRAME_GAP_S`) and **always read the program back
and compare**. Smallest gap that worked: 20 ms (`FRAME_GAP_MIN_S`). All ordinary uploads (150-400 ms gaps) were exact.

## Hidden features

Found: per-channel writes (opcode 0x04, channel 0-2); brightness 0 (fully dark); preview over any mode; brightness scales scenarios
and programs; single-point programs; DEMO semantics above.

Looked for and **not there**: effect speed (opcode 0x03 is a no-op); scenario index 8+ (dark); a ninth program or more DIY slots
(ids probed: 3-7, 127, 131-136, 140, 160, 192, 255 → aliases of the three slots or silence); weekday or other flags in the DIY header
(bytes ignored); version, model or any extra state: query opcodes `BD 06 03..1F FF FF FF FF` (0x0A skipped, it is the key check)
were all silent.

## Not tested (deliberately or not possible)

Rename and change-key (encoder only, as instructed); a refused key (`8A 00`); programs of more than 12 points; write-with-response;
what physical buttons do to the notifications; whether programs survive a power cut; the light's own factory-reset (Hour + / Minute −).

## Measurement method

A camera in the plant room sees the tank. The harness fetched a snapshot from HA, took the mean luma of a fixed box over the tank
and divided it by the mean luma of a fixed box on the litter robot (lit only by the camera's IR). Ratio: 0.474 lamp off or dark,
about 0.96 green at 90 %, about 1.4 everything on. The snapshots refresh only every ~2.7 s, so this resolves seconds, not flashes.
When the reference region disturbed the ratio (litter robot cycling), raw luma inside one session was used instead.

## State restore

Original state, read first: `bd0a81015a000007006400000e` (on, brightness 90, speed 0, manual, `sel` 7, colour 0/100/0, white 0);
DIY 1-3 all empty. Final state after all tests, read from the light: `bd0a81015a000007006400000e`, identical byte for byte (appendix, sections "FINAL");
DIY 1-3 were emptied again (header with count 0) and read back empty; the light's clock was synced to local time at the end.

## Appendix: live log (every command and reply)

Real light `PY4C-BT-PSTL` (`78:9C:E7:2D:FB:A3`) through BlueShark raw writes, 2026-09-30 00:58-02:20 local time, in the order the tests ran. `TX` is what was written, `RX` the first notification BlueShark returned (`-` = none within the wait; every `0xAD` command is unanswered by design). `+listen` rows show the notifications that followed a program query (BlueShark only returns the first). Repeated `verify_key` / `re-key` rows with no reply are the light ignoring a second key on an already authenticated link; a query that got no reply after an idle disconnect was retried once after a new key (harness behaviour). `[cam]` rows are the camera measurement described above (tank brightness / reference brightness).

### Initial state capture (first contact, before any change)

```
01:02:26  verify_key
          TX bd060a78563412ffe0  RX -
01:02:26  ORIGINAL status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e
01:02:40  ORIGINAL query_program 3
          TX bd060280ffffffff41  RX bd06828000007f074b
01:02:40  ORIGINAL query_program 4
          TX bd060281ffffffff42  RX bd06828100007f074c
01:02:40  ORIGINAL query_program 5
          TX bd060282ffffffff43  RX bd06828200007f074d
01:02:59  query_program 0 (preset)
          TX bd060200ffffffffc1  RX -
01:03:02  re-key
          TX bd060a78563412ffe0  RX -
01:03:04  query_program 0 (preset)
          TX bd060200ffffffffc1  RX -
01:03:07  query_program 1 (preset)
          TX bd060201ffffffffc2  RX -
01:03:09  re-key
          TX bd060a78563412ffe0  RX -
01:03:12  query_program 1 (preset)
          TX bd060201ffffffffc2  RX -
01:03:14  query_program 2 (preset)
          TX bd060202ffffffffc3  RX -
01:03:17  re-key
          TX bd060a78563412ffe0  RX -
01:03:19  query_program 2 (preset)
          TX bd060202ffffffffc3  RX -
01:10:28  [cam] scenario 3 on: luma 105.4  (early sample: no reference region yet)
01:10:30  [cam] power off: luma 49.4  (early sample: no reference region yet)
01:10:33  [cam] manual green on: luma 67.0  (early sample: no reference region yet)
01:10:38  [cam] all 100 brightness 100: luma 199.1  (early sample: no reference region yet)
01:12:23  [cam] current manual green 90: ratio 0.962 0.963  (luma 134.8, ref 140.0)
01:12:33  [cam] power off: ratio 0.474 0.474  (luma 67.2, ref 141.9)
01:12:44  [cam] manual green 90: ratio 0.960 0.959  (luma 137.8, ref 143.7)
01:12:55  [cam] green + white 100: ratio 1.384 1.381  (luma 191.3, ref 138.5)
01:13:05  [cam] green 90 again: ratio 0.961 0.964  (luma 135.6, ref 140.7)
```

### baseline

```
01:04:55  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:04:55  status (via protocol.py)
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### A power

```
01:05:01  verify_key
          TX bd060a78563412ffe0  RX -
01:05:01  A0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:05:02  A1 power(False)
          TX ad060100ffffffffb0  RX -
01:05:03  re-key
          TX bd060a78563412ffe0  RX -
01:05:04  A1 power(False)
          TX ad060100ffffffffb0  RX -
01:05:05  A2 status after power off
          TX bd0601ffffffffffbf  RX bd0a81005a000007006400000d  [status off bright=90 speed=0 manual RGBW=0,100,0,0]
01:05:08  A3 power(True)
          TX ad060101ffffffffb1  RX -
01:05:08  re-key
          TX bd060a78563412ffe0  RX -
01:05:10  A3 power(True)
          TX ad060101ffffffffb1  RX -
01:05:10  A4 status after power on
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### B brightness

```
01:05:24  verify_key
          TX bd060a78563412ffe0  RX -
01:05:24  B0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:05:25  B brightness(60)
          TX ad06023cffffffffed  RX -
01:05:25  B status after brightness 60
          TX bd0601ffffffffffbf  RX bd0a81013c00000700640000f0  [status on bright=60 speed=0 manual RGBW=0,100,0,0]
01:05:26  B brightness(100)
          TX ad060264ffffffff15  RX -
01:05:26  B status after brightness 100
          TX bd0601ffffffffffbf  RX bd0a8101640000070064000018  [status on bright=100 speed=0 manual RGBW=0,100,0,0]
01:05:27  B brightness(1)
          TX ad060201ffffffffb2  RX -
01:05:28  B status after brightness 1
          TX bd0601ffffffffffbf  RX bd0a81010100000700640000b5  [status on bright=1 speed=0 manual RGBW=0,100,0,0]
01:05:28  B brightness(90)
          TX ad06025affffffff0b  RX -
01:05:29  B status after brightness 90
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### B brightness hidden

```
01:05:37  verify_key
          TX bd060a78563412ffe0  RX -
01:05:37  B0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:05:37  ## raw brightness 0 (outside the app slider range 1..100)
01:05:37  B brightness(0) raw
          TX ad060200ffffffffb1  RX -
01:05:38  B status after brightness 0
          TX bd0601ffffffffffbf  RX bd0a81010000000700640000b4  [status on bright=0 speed=0 manual RGBW=0,100,0,0]
01:05:39  B brightness(90) restore
          TX ad06025affffffff0b  RX -
01:05:40  B status after restore 90
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### C colour+white

```
01:05:49  verify_key
          TX bd060a78563412ffe0  RX -
01:05:49  C0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:05:49  C color(0, 0, 100)
          TX ad0605000064ffff1a  RX -
01:05:50  C status after color (0, 0, 100)
          TX bd0601ffffffffffbf  RX bd0a81015a000007000064000e  [status on bright=90 speed=0 manual RGBW=0,0,100,0]
01:05:51  C color(100, 0, 0)
          TX ad0605640000ffff1a  RX -
01:05:51  C status after color (100, 0, 0)
          TX bd0601ffffffffffbf  RX bd0a81015a000007640000000e  [status on bright=90 speed=0 manual RGBW=100,0,0,0]
01:05:52  C color(37, 62, 81)
          TX ad0605253e51ffff6a  RX -
01:05:52  C status after color (37, 62, 81)
          TX bd0601ffffffffffbf  RX bd0a81015a000007253e51005e  [status on bright=90 speed=0 manual RGBW=37,62,81,0]
01:05:53  C color(0, 100, 0)
          TX ad0605006400ffff1a  RX -
01:05:53  C status after color (0, 100, 0)
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:05:54  C white(20)
          TX ad06041403ffffffcb  RX -
01:05:55  C status after white 20
          TX bd0601ffffffffffbf  RX bd0a81015a0000070064001422  [status on bright=90 speed=0 manual RGBW=0,100,0,20]
01:05:55  C white(55)
          TX ad06043703ffffffee  RX -
01:05:56  C status after white 55
          TX bd0601ffffffffffbf  RX bd0a81015a0000070064003745  [status on bright=90 speed=0 manual RGBW=0,100,0,55]
01:05:56  C white(0)
          TX ad06040003ffffffb7  RX -
01:05:57  C status after white 0
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### D scenarios

```
01:06:04  verify_key
          TX bd060a78563412ffe0  RX -
01:06:04  D0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:06:04  D scenario(0) Cloudy
          TX ad060700ffffffffb6  RX -
01:06:04  D status ~0.5s after scenario 0
          TX bd0601ffffffffffbf  RX bd0a81015a0001000064000008  [status on bright=90 speed=0 scenario scenario=0 RGBW=0,100,0,0]
01:06:05  D status ~1.2s after scenario 0
          TX bd0601ffffffffffbf  RX bd0a81015a0001000064000008  [status on bright=90 speed=0 scenario scenario=0 RGBW=0,100,0,0]
01:06:05  D scenario(1) Thunderstorm
          TX ad060701ffffffffb7  RX -
01:06:06  D status ~0.5s after scenario 1
          TX bd0601ffffffffffbf  RX bd0a81015a0001010064000009  [status on bright=90 speed=0 scenario scenario=1 RGBW=0,100,0,0]
01:06:07  D status ~1.2s after scenario 1
          TX bd0601ffffffffffbf  RX bd0a81015a0001010064000009  [status on bright=90 speed=0 scenario scenario=1 RGBW=0,100,0,0]
01:06:07  D scenario(2) Sunny
          TX ad060702ffffffffb8  RX -
01:06:08  D status ~0.5s after scenario 2
          TX bd0601ffffffffffbf  RX bd0a81015a000102006400000a  [status on bright=90 speed=0 scenario scenario=2 RGBW=0,100,0,0]
01:06:08  D status ~1.2s after scenario 2
          TX bd0601ffffffffffbf  RX bd0a81015a000102006400000a  [status on bright=90 speed=0 scenario scenario=2 RGBW=0,100,0,0]
01:06:09  D scenario(3) Moonlight
          TX ad060703ffffffffb9  RX -
01:06:09  D status ~0.5s after scenario 3
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:06:10  D status ~1.2s after scenario 3
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:06:10  D scenario(4) Warm White
          TX ad060704ffffffffba  RX -
01:06:10  D status ~0.5s after scenario 4
          TX bd0601ffffffffffbf  RX bd0a81015a000104006400000c  [status on bright=90 speed=0 scenario scenario=4 RGBW=0,100,0,0]
01:06:11  D status ~1.2s after scenario 4
          TX bd0601ffffffffffbf  RX bd0a81015a000104006400000c  [status on bright=90 speed=0 scenario scenario=4 RGBW=0,100,0,0]
01:06:11  D scenario(5) Bright White
          TX ad060705ffffffffbb  RX -
01:06:12  D status ~0.5s after scenario 5
          TX bd0601ffffffffffbf  RX bd0a81015a000105006400000d  [status on bright=90 speed=0 scenario scenario=5 RGBW=0,100,0,0]
01:06:13  D status ~1.2s after scenario 5
          TX bd0601ffffffffffbf  RX bd0a81015a000105006400000d  [status on bright=90 speed=0 scenario scenario=5 RGBW=0,100,0,0]
01:06:13  D scenario(6) Day
          TX ad060706ffffffffbc  RX -
01:06:13  D status ~0.5s after scenario 6
          TX bd0601ffffffffffbf  RX bd0a81015a000106006400000e  [status on bright=90 speed=0 scenario scenario=6 RGBW=0,100,0,0]
01:06:14  D status ~1.2s after scenario 6
          TX bd0601ffffffffffbf  RX bd0a81015a000106006400000e  [status on bright=90 speed=0 scenario scenario=6 RGBW=0,100,0,0]
01:06:14  D scenario(7) RGB Cycle
          TX ad060707ffffffffbd  RX -
01:06:15  D status ~0.5s after scenario 7
          TX bd0601ffffffffffbf  RX bd0a81015a000107006400000f  [status on bright=90 speed=0 scenario scenario=7 RGBW=0,100,0,0]
01:06:16  D status ~1.2s after scenario 7
          TX bd0601ffffffffffbf  RX bd0a81015a000107006400000f  [status on bright=90 speed=0 scenario scenario=7 RGBW=0,100,0,0]
```

### E mode switching

```
01:06:26  verify_key
          TX bd060a78563412ffe0  RX -
01:06:27  E0 status before (expect scenario 7)
          TX bd0601ffffffffffbf  RX bd0a81015a000107006400000f  [status on bright=90 speed=0 scenario scenario=7 RGBW=0,100,0,0]
01:06:27  ## brightness while in scenario mode
01:06:27  E1 brightness(80)
          TX ad060250ffffffff01  RX -
01:06:27  E1 status
          TX bd0601ffffffffffbf  RX bd0a8101500001070064000005  [status on bright=80 speed=0 scenario scenario=7 RGBW=0,100,0,0]
01:06:27  E1 brightness(90) restore
          TX ad06025affffffff0b  RX -
01:06:28  E1 status
          TX bd0601ffffffffffbf  RX bd0a81015a000107006400000f  [status on bright=90 speed=0 scenario scenario=7 RGBW=0,100,0,0]
01:06:28  ## white(0) while in scenario mode: does it switch to manual?
01:06:28  E2 white(0)
          TX ad06040003ffffffb7  RX -
01:06:29  E2 status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:06:29  ## colour while in scenario mode: does it switch to manual?
01:06:29  E3 color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:06:29  E3 status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### H sweep query opcodes

```
01:07:34  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:07:34  H0 status before sweep
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:07:34  ## blind sweep of QUERY opcodes BD 06 03..1F FF FF FF FF (0x0A skipped: it is the verify-key opcode, a wrong key could count as a failed login)
01:07:34  H sweep BD 06 03
          TX bd0603ffffffffffc1  RX -
01:07:35  H sweep BD 06 04
          TX bd0604ffffffffffc2  RX -
01:07:35  H sweep BD 06 05
          TX bd0605ffffffffffc3  RX -
01:07:35  H sweep BD 06 06
          TX bd0606ffffffffffc4  RX -
01:07:36  H sweep BD 06 07
          TX bd0607ffffffffffc5  RX -
01:07:36  H sweep BD 06 08
          TX bd0608ffffffffffc6  RX -
01:07:37  H sweep BD 06 09
          TX bd0609ffffffffffc7  RX -
01:07:37  H sweep BD 06 0B
          TX bd060bffffffffffc9  RX -
01:07:38  H sweep BD 06 0C
          TX bd060cffffffffffca  RX -
01:07:38  H sweep BD 06 0D
          TX bd060dffffffffffcb  RX -
01:07:39  H sweep BD 06 0E
          TX bd060effffffffffcc  RX -
01:07:39  H sweep BD 06 0F
          TX bd060fffffffffffcd  RX -
01:07:40  H sweep BD 06 10
          TX bd0610ffffffffffce  RX -
01:07:40  H sweep BD 06 11
          TX bd0611ffffffffffcf  RX -
01:07:40  H sweep BD 06 12
          TX bd0612ffffffffffd0  RX -
01:07:41  H sweep BD 06 13
          TX bd0613ffffffffffd1  RX -
01:07:41  H sweep BD 06 14
          TX bd0614ffffffffffd2  RX -
01:07:42  H sweep BD 06 15
          TX bd0615ffffffffffd3  RX -
01:07:42  H sweep BD 06 16
          TX bd0616ffffffffffd4  RX -
01:07:43  H sweep BD 06 17
          TX bd0617ffffffffffd5  RX -
01:07:43  H sweep BD 06 18
          TX bd0618ffffffffffd6  RX -
01:07:44  H sweep BD 06 19
          TX bd0619ffffffffffd7  RX -
01:07:44  H sweep BD 06 1A
          TX bd061affffffffffd8  RX -
01:07:44  H sweep BD 06 1B
          TX bd061bffffffffffd9  RX -
01:07:45  H sweep BD 06 1C
          TX bd061cffffffffffda  RX -
01:07:45  H sweep BD 06 1D
          TX bd061dffffffffffdb  RX -
01:07:46  H sweep BD 06 1E
          TX bd061effffffffffdc  RX -
01:07:46  H sweep BD 06 1F
          TX bd061fffffffffffdd  RX -
01:07:46  ## answered opcodes: {}
01:07:46  H1 status after sweep
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### F mode flips + power off writes

```
01:08:02  verify_key
          TX bd060a78563412ffe0  RX -
01:08:02  F0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:08:02  ## F1: colour alone from scenario mode (does 0x05 flip to manual?)
01:08:02  F1 scenario(3)
          TX ad060703ffffffffb9  RX -
01:08:03  F1 status in scenario 3
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:08:03  F1 color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:08:04  F1 status after colour
          TX bd0601ffffffffffbf  RX bd0a81015a000003006400000a  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:08:04  ## F2: white alone from scenario mode (repeat with a non-zero value)
01:08:04  F2 scenario(3)
          TX ad060703ffffffffb9  RX -
01:08:04  F2 status in scenario 3
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:08:04  F2 white(10)
          TX ad06040a03ffffffc1  RX -
01:08:05  F2 status after white 10
          TX bd0601ffffffffffbf  RX bd0a81015a0000030064000a14  [status on bright=90 speed=0 manual RGBW=0,100,0,10]
01:08:05  F2 white(0)
          TX ad06040003ffffffb7  RX -
01:08:05  F2 status after white 0
          TX bd0601ffffffffffbf  RX bd0a81015a000003006400000a  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### F3 writes while power is off

```
01:08:13  verify_key
          TX bd060a78563412ffe0  RX -
01:08:13  F3 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000003006400000a  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:08:13  F3 power(False)
          TX ad060100ffffffffb0  RX -
01:08:14  F3 status off
          TX bd0601ffffffffffbf  RX bd0a81005a0000030064000009  [status off bright=90 speed=0 manual RGBW=0,100,0,0]
01:08:14  F3 color(10,90,0) while off
          TX ad06050a5a00ffff1a  RX -
01:08:15  F3 status after colour (off)
          TX bd0601ffffffffffbf  RX bd0a81005a0000030a5a000009  [status off bright=90 speed=0 manual RGBW=10,90,0,0]
01:08:15  F3 white(5) while off
          TX ad06040503ffffffbc  RX -
01:08:15  F3 status after white (off)
          TX bd0601ffffffffffbf  RX bd0a81005a0000030a5a00050e  [status off bright=90 speed=0 manual RGBW=10,90,0,5]
01:08:15  F3 brightness(85) while off
          TX ad060255ffffffff06  RX -
01:08:16  F3 status after brightness (off)
          TX bd0601ffffffffffbf  RX bd0a8100550000030a5a000509  [status off bright=85 speed=0 manual RGBW=10,90,0,5]
01:08:16  F3 scenario(6) while off
          TX ad060706ffffffffbc  RX -
01:08:16  F3 status after scenario (off)
          TX bd0601ffffffffffbf  RX bd0a8100550001060a5a00050d  [status off bright=85 speed=0 scenario scenario=6 RGBW=10,90,0,5]
01:08:16  ## restore: colour/white/brightness manual values, then power on
01:08:17  F3 color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:08:17  F3 white(0)
          TX ad06040003ffffffb7  RX -
01:08:17  F3 brightness(90)
          TX ad06025affffffff0b  RX -
01:08:17  F3 status restored values (still off)
          TX bd0601ffffffffffbf  RX bd0a81005a000006006400000c  [status off bright=90 speed=0 manual RGBW=0,100,0,0]
01:08:17  F3 power(True)
          TX ad060101ffffffffb1  RX -
01:08:18  F3 status power on
          TX bd0601ffffffffffbf  RX bd0a81015a000006006400000d  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### G time sync + program select

```
01:08:28  verify_key
          TX bd060a78563412ffe0  RX -
01:08:29  G0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000006006400000d  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:08:29  ## time sync with host local time 2026-09-30T01:08:29 (America/New_York), isoweekday=3
01:08:29  G1 time_sync(01:08:29 dow 3)
          TX ad060601081d03ffe1  RX -
01:08:29  G1 status after time sync
          TX bd0601ffffffffffbf  RX bd0a81015a000006006400000d  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:08:29  G2 program(0) Japanese style
          TX ad060800ffffffffb7  RX -
01:08:30  G2 status after program 0
          TX bd0601ffffffffffbf  RX bd0a81015a0002000064000009  [status on bright=90 speed=0 program program=0 RGBW=0,100,0,0]
01:08:31  G2 status after program 0 (+1 s)
          TX bd0601ffffffffffbf  RX bd0a81015a0002000064000009  [status on bright=90 speed=0 program program=0 RGBW=0,100,0,0]
01:08:31  G2 program(1) Dutch style
          TX ad060801ffffffffb8  RX -
01:08:32  G2 status after program 1
          TX bd0601ffffffffffbf  RX bd0a81015a000201006400000a  [status on bright=90 speed=0 program program=1 RGBW=0,100,0,0]
01:08:33  G2 status after program 1 (+1 s)
          TX bd0601ffffffffffbf  RX bd0a81015a000201006400000a  [status on bright=90 speed=0 program program=1 RGBW=0,100,0,0]
01:08:33  G2 program(2) Jungle style
          TX ad060802ffffffffb9  RX -
01:08:34  G2 status after program 2
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
01:08:35  G2 status after program 2 (+1 s)
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
```

### G3 program-mode hidden behaviour

```
01:08:48  verify_key
          TX bd060a78563412ffe0  RX -
01:08:48  G3 status before (expect program 2)
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
01:08:48  ## brightness while in program mode (hidden feature)
01:08:48  G3 brightness(80)
          TX ad060250ffffffff01  RX -
01:08:49  G3 status after brightness 80
          TX bd0601ffffffffffbf  RX bd0a8101500002020064000001  [status on bright=80 speed=0 program program=2 RGBW=0,100,0,0]
01:08:49  G3 brightness(90) restore
          TX ad06025affffffff0b  RX -
01:08:49  G3 status after brightness 90
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
01:08:49  ## demo while in program mode (app allows this); poll the mode byte for ~10 s
01:08:50  G3 demo()
          TX ad060bffffffffffb9  RX -
01:08:51  G3 status demo +2s
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
01:08:53  G3 status demo +4s
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
01:08:55  G3 status demo +6s
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
01:08:57  G3 status demo +8s
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
01:08:59  G3 status demo +10s
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
```

### G4 demo outside program mode

```
01:09:09  verify_key
          TX bd060a78563412ffe0  RX -
01:09:09  G4 status before (program 2)
          TX bd0601ffffffffffbf  RX bd0a81015a000202006400000b  [status on bright=90 speed=0 program program=2 RGBW=0,100,0,0]
01:09:09  ## demo from MANUAL mode
01:09:09  G4 color(0,100,0) -> manual
          TX ad0605006400ffff1a  RX -
01:09:10  G4 status manual
          TX bd0601ffffffffffbf  RX bd0a81015a0000020064000009  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:09:10  G4 demo() in manual
          TX ad060bffffffffffb9  RX -
01:09:12  G4 manual demo +2s
          TX bd0601ffffffffffbf  RX bd0a81015a0000020064000009  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:09:14  G4 manual demo +4s
          TX bd0601ffffffffffbf  RX bd0a81015a0000020064000009  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:09:16  G4 manual demo +6s
          TX bd0601ffffffffffbf  RX bd0a81015a0000020064000009  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:09:16  ## demo from SCENARIO mode
01:09:16  G4 scenario(3)
          TX ad060703ffffffffb9  RX -
01:09:16  G4 status scenario 3
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:09:16  G4 demo() in scenario
          TX ad060bffffffffffb9  RX -
01:09:18  G4 scenario demo +2s
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:09:20  G4 scenario demo +4s
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:09:22  G4 scenario demo +6s
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
```

### V visual calibration (camera)

```
01:10:26  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:10:26  V0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:10:26  ## camera ROI luma calibration: scenario 3 (as left), power off, manual green 90%, manual white 100%
01:10:28  V power(False)
          TX ad060100ffffffffb0  RX -
01:10:31  V color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:10:31  V power(True)
          TX ad060101ffffffffb1  RX -
01:10:33  V color(100,100,100)
          TX ad0605646464ffffe2  RX -
01:10:34  V white(100)
          TX ad06046403ffffff1b  RX -
01:10:34  V brightness(100)
          TX ad060264ffffffff15  RX -
01:10:39  V brightness(90)
          TX ad06025affffffff0b  RX -
01:10:39  V white(0)
          TX ad06040003ffffffb7  RX -
01:10:39  V color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:10:39  V status restored
          TX bd0601ffffffffffbf  RX bd0a81015a000003006400000a  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### V camera settle check

```
01:11:26  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:11:27  V status now
          TX bd0601ffffffffffbf  RX bd0a81015a000003006400000a  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### V visual calibration 2

```
01:12:19  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:12:23  V power(False)
          TX ad060100ffffffffb0  RX -
01:12:33  V power(True)
          TX ad060101ffffffffb1  RX -
01:12:44  V white(100)
          TX ad06046403ffffff1b  RX -
01:12:55  V white(0)
          TX ad06040003ffffffb7  RX -
01:13:06  V status
          TX bd0601ffffffffffbf  RX bd0a81015a000003006400000a  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### V1 brightness scale (camera)

```
01:13:45  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:13:45  V1 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000003006400000a  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:13:45  V1 brightness(0)
          TX ad060200ffffffffb1  RX -
01:13:53  [cam] brightness 0: ratio 0.473 0.475  (luma 65.9, ref 138.8)
01:13:54  V1 brightness(1)
          TX ad060201ffffffffb2  RX -
01:14:01  [cam] brightness 1: ratio 0.494 0.492  (luma 68.3, ref 138.8)
01:14:02  V1 brightness(10)
          TX ad06020affffffffbb  RX -
01:14:09  [cam] brightness 10: ratio 0.603 0.598  (luma 83.3, ref 139.2)
01:14:10  V1 brightness(50)
          TX ad060232ffffffffe3  RX -
01:14:17  [cam] brightness 50: ratio 0.825 0.821  (luma 115.1, ref 140.1)
01:14:18  V1 brightness(100)
          TX ad060264ffffffff15  RX -
01:14:25  [cam] brightness 100: ratio 0.990 0.986  (luma 139.3, ref 141.2)
01:14:26  V1 brightness(90)
          TX ad06025affffffff0b  RX -
01:14:33  [cam] brightness 90: ratio 0.966 0.965  (luma 135.8, ref 140.7)
01:14:34  V1 status after restore
          TX bd0601ffffffffffbf  RX bd0a81015a000003006400000a  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### V2 scenarios (camera)

```
01:14:42  verify_key
          TX bd060a78563412ffe0  RX -
01:14:42  V2 scenario(0) Cloudy
          TX ad060700ffffffffb6  RX -
01:14:52  [cam] camera series 'scenario 0 Cloudy' (10 samples, changes >= 0.012 shown): 0s=0.965 1.8s=1.127 8.1s=1.127
01:14:52  V2 scenario(1) Thunderstorm
          TX ad060701ffffffffb7  RX -
01:15:03  [cam] camera series 'scenario 1 Thunderstorm' (10 samples, changes >= 0.012 shown): 0s=1.427 2.7s=0.843 5.4s=0.877 8.1s=1.345
01:15:03  V2 scenario(2) Sunny
          TX ad060702ffffffffb8  RX -
01:15:14  [cam] camera series 'scenario 2 Sunny' (10 samples, changes >= 0.012 shown): 0s=1.404 2.7s=1.418 8.1s=1.421
01:15:14  V2 scenario(3) Moonlight
          TX ad060703ffffffffb9  RX -
01:15:24  [cam] camera series 'scenario 3 Moonlight' (10 samples, changes >= 0.012 shown): 0s=0.743 2.7s=0.758 8.1s=0.757
01:15:24  V2 scenario(4) Warm White
          TX ad060704ffffffffba  RX -
01:15:35  [cam] camera series 'scenario 4 Warm White' (10 samples, changes >= 0.012 shown): 0s=1.396 2.7s=1.360 8.1s=1.360
01:15:35  V2 scenario(5) Bright White
          TX ad060705ffffffffbb  RX -
01:15:46  [cam] camera series 'scenario 5 Bright White' (10 samples, changes >= 0.012 shown): 0s=1.384 2.7s=1.355 8.1s=1.352
01:15:46  V2 scenario(6) Day
          TX ad060706ffffffffbc  RX -
01:15:56  [cam] camera series 'scenario 6 Day' (10 samples, changes >= 0.012 shown): 0s=1.362 2.7s=1.342 8.1s=1.339
01:15:56  V2 scenario(7) RGB Cycle
          TX ad060707ffffffffbd  RX -
01:16:07  [cam] camera series 'scenario 7 RGB Cycle' (10 samples, changes >= 0.012 shown): 0s=0.447 2.7s=0.884 5.4s=0.929 7.2s=0.802 8.1s=0.802
01:16:07  V2 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:16:07  V2 status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### I speed readback

```
01:16:32  verify_key
          TX bd060a78563412ffe0  RX -
01:16:32  I0 status before (manual, speed 0)
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:32  I speed(1)
          TX ad060301ffffffffb3  RX -
01:16:32  I status after speed 1
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:32  I speed(2)
          TX ad060302ffffffffb4  RX -
01:16:33  I status after speed 2
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:33  I speed(3)
          TX ad060303ffffffffb5  RX -
01:16:33  I status after speed 3
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:33  I speed(4)
          TX ad060304ffffffffb6  RX -
01:16:33  I status after speed 4
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:34  I speed(5)
          TX ad060305ffffffffb7  RX -
01:16:34  I status after speed 5
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:34  I speed(8)
          TX ad060308ffffffffba  RX -
01:16:34  I status after speed 8
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:35  I speed(9)
          TX ad060309ffffffffbb  RX -
01:16:35  I status after speed 9
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:35  I speed(10)
          TX ad06030affffffffbc  RX -
01:16:35  I status after speed 10
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:35  I speed(11)
          TX ad06030bffffffffbd  RX -
01:16:36  I status after speed 11
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:36  I speed(15)
          TX ad06030fffffffffc1  RX -
01:16:36  I status after speed 15
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:36  I speed(20)
          TX ad060314ffffffffc6  RX -
01:16:37  I status after speed 20
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:37  I speed(50)
          TX ad060332ffffffffe4  RX -
01:16:37  I status after speed 50
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:37  I speed(100)
          TX ad060364ffffffff16  RX -
01:16:38  I status after speed 100
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:38  I speed(101)
          TX ad060365ffffffff17  RX -
01:16:38  I status after speed 101
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:38  I speed(127)
          TX ad06037fffffffff31  RX -
01:16:38  I status after speed 127
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:39  I speed(128)
          TX ad060380ffffffff32  RX -
01:16:39  I status after speed 128
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:39  I speed(200)
          TX ad0603c8ffffffff7a  RX -
01:16:39  I status after speed 200
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:39  I speed(255)
          TX ad0603ffffffffffb1  RX -
01:16:40  I status after speed 255
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:16:40  I speed(0)
          TX ad060300ffffffffb2  RX -
01:16:40  I status after speed 0
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### I2 speed visual (RGB cycle)

```
01:16:53  verify_key
          TX bd060a78563412ffe0  RX -
01:16:54  I2 scenario(7) RGB Cycle
          TX ad060707ffffffffbd  RX -
01:16:54  I2 speed(0)
          TX ad060300ffffffffb2  RX -
01:17:08  [cam] camera series 'RGB cycle speed 0' (20 samples, changes >= 0.012 shown): 0s=0.789 1.5s=0.989 4s=0.738 6.5s=0.912 9.5s=0.919
01:17:09  I2 speed(255)
          TX ad0603ffffffffffb1  RX -
01:17:22  [cam] camera series 'RGB cycle speed 255' (20 samples, changes >= 0.012 shown): 0s=0.807 1s=0.992 3.5s=0.756 6.5s=0.836 9s=0.951 9.5s=0.951
01:17:22  I2 speed(1)
          TX ad060301ffffffffb3  RX -
01:17:35  [cam] camera series 'RGB cycle speed 1' (20 samples, changes >= 0.012 shown): 0s=0.789 1s=0.977 3.5s=0.830 6s=0.804 9s=0.977 9.5s=0.977
01:17:35  I2 speed(0) restore
          TX ad060300ffffffffb2  RX -
01:17:35  I2 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:17:36  I2 status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### J preview

```
01:17:55  verify_key
          TX bd060a78563412ffe0  RX -
01:17:56  J0 status before (manual green 90)
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:17:59  [cam] J0 baseline manual green: ratio 0.961  (luma 138.2, ref 143.8)
01:17:59  J1 preview(0,0,100,0)
          TX ad060c010000640024  RX -
01:18:00  J1 status during preview
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:18:07  [cam] J1 preview blue: ratio 0.700 0.698  (luma 99.4, ref 142.3)
01:18:07  J2 preview end
          TX ad060c0000000000bf  RX -
01:18:08  J2 status after preview end
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:18:15  [cam] J2 after preview end: ratio 0.964 0.959  (luma 138.3, ref 144.2)
01:18:15  ## preview on top of scenario 3 (moonlight)
01:18:15  J3 scenario(3)
          TX ad060703ffffffffb9  RX -
01:18:23  [cam] J3 scenario 3 baseline: ratio 0.763 0.760  (luma 108.5, ref 142.7)
01:18:23  J3 preview(100,0,0,0) red
          TX ad060c016400000024  RX -
01:18:24  J3 status during preview
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:18:31  [cam] J3 preview red over scenario: ratio 0.779 0.777  (luma 111.1, ref 143.0)
01:18:31  J3 preview end
          TX ad060c0000000000bf  RX -
01:18:32  J3 status after preview end
          TX bd0601ffffffffffbf  RX bd0a81015a000103006400000b  [status on bright=90 speed=0 scenario scenario=3 RGBW=0,100,0,0]
01:18:39  [cam] J3 after preview end (scenario 3): ratio 0.763 0.762  (luma 108.6, ref 142.4)
01:18:39  ## preview on top of program 0 (Japanese, dark at night)
01:18:39  J4 program(0)
          TX ad060800ffffffffb7  RX -
01:18:47  [cam] J4 program 0 baseline: ratio 0.472 0.475  (luma 67.3, ref 141.7)
01:18:47  J4 preview(0,100,0,0) green
          TX ad060c010064000024  RX -
01:18:48  J4 status during preview
          TX bd0601ffffffffffbf  RX bd0a81015a0002000064000009  [status on bright=90 speed=0 program program=0 RGBW=0,100,0,0]
01:18:55  [cam] J4 preview green over program: ratio 0.962 0.962  (luma 138.4, ref 143.8)
01:18:55  J4 preview end
          TX ad060c0000000000bf  RX -
01:18:56  J4 status after preview end
          TX bd0601ffffffffffbf  RX bd0a81015a0002000064000009  [status on bright=90 speed=0 program program=0 RGBW=0,100,0,0]
01:19:03  [cam] J4 after preview end (program 0): ratio 0.472 0.475  (luma 67.3, ref 141.7)
01:19:03  J5 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:19:04  J5 status restored
          TX bd0601ffffffffffbf  RX bd0a81015a0000000064000007  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### K hidden: opcode 0x04 channel byte

```
01:19:17  verify_key
          TX bd060a78563412ffe0  RX -
01:19:17  K0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a0000000064000007  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:19:17  ## dark manual base: color(0,0,0) white(0)
01:19:17  K color(0,0,0)
          TX ad0605000000ffffb6  RX -
01:19:17  K white(0)
          TX ad06040003ffffffb7  RX -
01:19:18  K status dark base
          TX bd0601ffffffffffbf  RX bd0a81015a00000000000000a3  [status on bright=90 speed=0 manual RGBW=0,0,0,0]
01:19:25  [cam] K dark base: ratio 0.470 0.474  (luma 67.1, ref 141.7)
01:19:25  K chan(60, chl=0)
          TX ad06043c00fffffff0  RX -
01:19:25  K status after chan chl=0
          TX bd0601ffffffffffbf  RX bd0a81015a0000003c000000df  [status on bright=90 speed=0 manual RGBW=60,0,0,0]
01:19:33  [cam] K chl 0 value 60: ratio 0.696 0.696  (luma 99.1, ref 142.4)
01:19:33  K color(0,0,0) reset
          TX ad0605000000ffffb6  RX -
01:19:33  K white(0) reset
          TX ad06040003ffffffb7  RX -
01:19:33  K chan(60, chl=1)
          TX ad06043c01fffffff1  RX -
01:19:33  K status after chan chl=1
          TX bd0601ffffffffffbf  RX bd0a81015a000000003c0000df  [status on bright=90 speed=0 manual RGBW=0,60,0,0]
01:19:41  [cam] K chl 1 value 60: ratio 0.847 0.845  (luma 120.8, ref 143.0)
01:19:41  K color(0,0,0) reset
          TX ad0605000000ffffb6  RX -
01:19:41  K white(0) reset
          TX ad06040003ffffffb7  RX -
01:19:41  K chan(60, chl=2)
          TX ad06043c02fffffff2  RX -
01:19:41  K status after chan chl=2
          TX bd0601ffffffffffbf  RX bd0a81015a00000000003c00df  [status on bright=90 speed=0 manual RGBW=0,0,60,0]
01:19:49  [cam] K chl 2 value 60: ratio 0.627 0.624  (luma 88.5, ref 141.9)
01:19:49  K color(0,0,0) reset
          TX ad0605000000ffffb6  RX -
01:19:49  K white(0) reset
          TX ad06040003ffffffb7  RX -
01:19:49  ## reference: known white channel chl=3 value 60
01:19:49  K white(60)
          TX ad06043c03fffffff3  RX -
01:19:49  K status white 60
          TX bd0601ffffffffffbf  RX bd0a81015a0000000000003cdf  [status on bright=90 speed=0 manual RGBW=0,0,0,60]
01:19:57  [cam] K white 60 (chl 3): ratio 1.204 1.205  (luma 168.4, ref 139.7)
01:19:57  K white(0)
          TX ad06040003ffffffb7  RX -
01:19:57  K color(0,100,0) restore
          TX ad0605006400ffff1a  RX -
01:19:58  K status restored
          TX bd0601ffffffffffbf  RX bd0a81015a0000000064000007  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### L hidden: scenario index 8

```
01:20:09  verify_key
          TX bd060a78563412ffe0  RX -
01:20:09  L0 status before (manual green)
          TX bd0601ffffffffffbf  RX bd0a81015a0000000064000007  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:20:13  [cam] L0 baseline manual green: ratio 0.965  (luma 135.5, ref 140.4)
01:20:13  ## single try: scenario index 8 (raw frame, outside the app range 0-7)
01:20:13  L scenario(8) raw
          TX ad060708ffffffffbe  RX -
01:20:13  L status after scenario 8
          TX bd0601ffffffffffbf  RX bd0a81015a0001080064000010  [status on bright=90 speed=0 scenario scenario=None RGBW=0,100,0,0]
01:20:21  [cam] camera series 'scenario 8 (raw)' (8 samples, changes >= 0.012 shown): 0s=0.346 2.7s=0.472 6.3s=0.473
01:20:21  L status after 7 s
          TX bd0601ffffffffffbf  RX bd0a81015a0001080064000010  [status on bright=90 speed=0 scenario scenario=None RGBW=0,100,0,0]
01:20:21  ## restore: colour -> manual
01:20:21  L color(0,100,0) restore
          TX ad0605006400ffff1a  RX -
01:20:22  L status restored
          TX bd0601ffffffffffbf  RX bd0a81015a000008006400000f  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:20:26  [cam] L restored manual green: ratio 0.964  (luma 138.2, ref 143.4)
```

### M DIY upload / readback (slot DIY 1, was empty)

```
01:20:47  verify_key
          TX bd060a78563412ffe0  RX -
01:20:47  M0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000008006400000f  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:20:47  M0 query_program(3) before (expect empty)
          TX bd060280ffffffff41  RX bd06828000007f084c  [program header DIY1 count=0]
01:20:47  M1 upload frame 0
          TX ad06098003007fffbd  RX -
01:20:47  M1 upload frame 1
          TX ad080a00060000000000c5  RX -
01:20:48  M1 upload frame 2
          TX ad080a010c1e64320a149e  RX -
01:20:48  M1 upload frame 3
          TX ad080a02173b050607082d  RX -
01:20:48  M2 query_program(3) after upload
          TX bd060280ffffffff41  RX bd06828003007f084f  [program header DIY1 count=3]
```

### M2 DIY readback via listen

```
01:22:04  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:22:05  query_program(3)
          TX bd060280ffffffff41  RX bd06828003007fff46  [program header DIY1 count=3]
01:22:06  query_program(3) +listen: first RX bd06828003007fff46  then 3 more: bd0883000600000000004e@0ms bd0883010c1e64320a1427@8ms bd088302173b05060708b6@35ms
```

### N DIY header trailing bytes (slot DIY 2, was empty)

```
01:22:35  verify_key
          TX bd060a78563412ffe0  RX -
01:22:35  N0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a000008006400000f  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:22:35  ## header variant app default 00 7F FF
01:22:35  N upload app default 00 7F FF frame 0
          TX ad06098103007fffbe  RX -
01:22:35  N upload app default 00 7F FF frame 1
          TX ad080a0000000064000023  RX -
01:22:35  N upload app default 00 7F FF frame 2
          TX ad080a010800006400002c  RX -
01:22:35  N upload app default 00 7F FF frame 3
          TX ad080a0210000064000035  RX -
01:22:36  N readback [app default 00 7F FF]
          TX bd060281ffffffff42  RX bd06828103007f0850  [program header DIY2 count=3]
01:22:37  N readback [app default 00 7F FF] +listen: first RX bd06828103007f0850  then 3 more: bd088300000000640000ac@8ms bd088301080000640000b5@89ms bd088302100000640000be@91ms
01:22:37  N program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:22:45  [cam] N DIY2 running [app default 00 7F FF]: ratio 0.964 0.961  (luma 137.6, ref 143.1)
01:22:45  ## header variant mask byte 00
01:22:45  N upload mask byte 00 frame 0
          TX ad060981030000ff3f  RX -
01:22:45  N upload mask byte 00 frame 1
          TX ad080a0000000064000023  RX -
01:22:45  N upload mask byte 00 frame 2
          TX ad080a010800006400002c  RX -
01:22:46  N upload mask byte 00 frame 3
          TX ad080a0210000064000035  RX -
01:22:46  N readback [mask byte 00]
          TX bd060281ffffffff42  RX bd06828103007f64ac  [program header DIY2 count=3]
01:22:47  N readback [mask byte 00] +listen: first RX bd06828103007f64ac  then 3 more: bd088300000000640000ac@0ms bd088301080000640000b5@9ms bd088302100000640000be@74ms
01:22:47  N program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:22:56  [cam] N DIY2 running [mask byte 00]: ratio 0.964 0.964  (luma 137.8, ref 142.9)
01:22:56  ## header variant 7B (all but bit2)
01:22:56  N upload 7B (all but bit2) frame 0
          TX ad06098103007bffba  RX -
01:22:56  N upload 7B (all but bit2) frame 1
          TX ad080a0000000064000023  RX -
01:22:56  N upload 7B (all but bit2) frame 2
          TX ad080a010800006400002c  RX -
01:22:56  N upload 7B (all but bit2) frame 3
          TX ad080a0210000064000035  RX -
01:22:56  N readback [7B (all but bit2)]
          TX bd060281ffffffff42  RX bd06828103007f64ac  [program header DIY2 count=3]
01:22:57  N readback [7B (all but bit2)] +listen: first RX bd06828103007f64ac  then 3 more: bd088300000000640000ac@0ms bd088301080000640000b5@83ms bd088302100000640000be@114ms
01:22:58  N program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:23:06  [cam] N DIY2 running [7B (all but bit2)]: ratio 0.964 0.959  (luma 137.8, ref 143.6)
01:23:06  ## header variant 04 (only bit2)
01:23:06  N upload 04 (only bit2) frame 0
          TX ad060981030004ff43  RX -
01:23:07  N upload 04 (only bit2) frame 1
          TX ad080a0000000064000023  RX -
01:23:07  N upload 04 (only bit2) frame 2
          TX ad080a010800006400002c  RX -
01:23:07  N upload 04 (only bit2) frame 3
          TX ad080a0210000064000035  RX -
01:23:07  N readback [04 (only bit2)]
          TX bd060281ffffffff42  RX bd06828103007f64ac  [program header DIY2 count=3]
01:23:08  N readback [04 (only bit2)] +listen: first RX bd06828103007f64ac  then 3 more: bd088300000000640000ac@4ms bd088301080000640000b5@8ms bd088302100000640000be@79ms
01:23:08  N program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:23:17  [cam] N DIY2 running [04 (only bit2)]: ratio 0.960 0.962  (luma 137.7, ref 143.2)
01:23:17  N color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:23:18  N status after
          TX bd0601ffffffffffbf  RX bd0a81015a0000810064000088  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### N2 DIY header trailing bytes, blue constant (slot DIY 2)

```
01:23:34  verify_key
          TX bd060a78563412ffe0  RX -
01:23:34  N2 status before (manual green)
          TX bd0601ffffffffffbf  RX bd0a81015a0000810064000088  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:23:36  [cam] N2 baseline manual green: ratio 0.959  (luma 137.7, ref 143.6)
01:23:36  ## header variant app default 00 7F FF
01:23:36  N2 upload app default 00 7F FF frame 0
          TX ad06098103007fffbe  RX -
01:23:36  N2 upload app default 00 7F FF frame 1
          TX ad080a0000000000640023  RX -
01:23:36  N2 upload app default 00 7F FF frame 2
          TX ad080a010800000064002c  RX -
01:23:36  N2 upload app default 00 7F FF frame 3
          TX ad080a0210000000640035  RX -
01:23:36  N2 program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:23:37  N2 status running [app default 00 7F FF]
          TX bd0601ffffffffffbf  RX bd0a81015a000281006400008a  [status on bright=90 speed=0 program program=4 RGBW=0,100,0,0]
01:23:46  [cam] N2 DIY2 blue running [app default 00 7F FF]: ratio 0.694 0.697  (luma 98.7, ref 141.6)
01:23:46  N2 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:23:47  ## header variant mask byte 00
01:23:47  N2 upload mask byte 00 frame 0
          TX ad060981030000ff3f  RX -
01:23:47  N2 upload mask byte 00 frame 1
          TX ad080a0000000000640023  RX -
01:23:47  N2 upload mask byte 00 frame 2
          TX ad080a010800000064002c  RX -
01:23:48  N2 upload mask byte 00 frame 3
          TX ad080a0210000000640035  RX -
01:23:48  N2 program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:23:48  N2 status running [mask byte 00]
          TX bd0601ffffffffffbf  RX bd0a81015a000281006400008a  [status on bright=90 speed=0 program program=4 RGBW=0,100,0,0]
01:23:57  [cam] N2 DIY2 blue running [mask byte 00]: ratio 0.698 0.696  (luma 98.7, ref 141.8)
01:23:57  N2 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:23:58  ## header variant 7B
01:23:58  N2 upload 7B frame 0
          TX ad06098103007bffba  RX -
01:23:58  N2 upload 7B frame 1
          TX ad080a0000000000640023  RX -
01:23:58  N2 upload 7B frame 2
          TX ad080a010800000064002c  RX -
01:23:58  N2 upload 7B frame 3
          TX ad080a0210000000640035  RX -
01:23:58  N2 program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:23:59  N2 status running [7B]
          TX bd0601ffffffffffbf  RX bd0a81015a000281006400008a  [status on bright=90 speed=0 program program=4 RGBW=0,100,0,0]
01:24:08  [cam] N2 DIY2 blue running [7B]: ratio 0.696 0.697  (luma 98.7, ref 141.7)
01:24:08  N2 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:24:08  ## header variant 04
01:24:09  N2 upload 04 frame 0
          TX ad060981030004ff43  RX -
01:24:09  N2 upload 04 frame 1
          TX ad080a0000000000640023  RX -
01:24:09  N2 upload 04 frame 2
          TX ad080a010800000064002c  RX -
01:24:09  N2 upload 04 frame 3
          TX ad080a0210000000640035  RX -
01:24:09  N2 program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:24:10  N2 status running [04]
          TX bd0601ffffffffffbf  RX bd0a81015a000281006400008a  [status on bright=90 speed=0 program program=4 RGBW=0,100,0,0]
01:24:18  [cam] N2 DIY2 blue running [04]: ratio 0.696 0.696  (luma 98.5, ref 141.6)
01:24:18  N2 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:24:19  ## header variant first byte 01
01:24:19  N2 upload first byte 01 frame 0
          TX ad06098103017fffbf  RX -
01:24:19  N2 upload first byte 01 frame 1
          TX ad080a0000000000640023  RX -
01:24:19  N2 upload first byte 01 frame 2
          TX ad080a010800000064002c  RX -
01:24:20  N2 upload first byte 01 frame 3
          TX ad080a0210000000640035  RX -
01:24:20  N2 program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:24:20  N2 status running [first byte 01]
          TX bd0601ffffffffffbf  RX bd0a81015a000281006400008a  [status on bright=90 speed=0 program program=4 RGBW=0,100,0,0]
01:24:29  [cam] N2 DIY2 blue running [first byte 01]: ratio 0.696 0.696  (luma 98.5, ref 141.5)
01:24:29  N2 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:24:30  ## header variant last byte 00
01:24:30  N2 upload last byte 00 frame 0
          TX ad06098103007f00bf  RX -
01:24:30  N2 upload last byte 00 frame 1
          TX ad080a0000000000640023  RX -
01:24:30  N2 upload last byte 00 frame 2
          TX ad080a010800000064002c  RX -
01:24:30  N2 upload last byte 00 frame 3
          TX ad080a0210000000640035  RX -
01:24:30  N2 program(4) select DIY 2
          TX ad060881ffffffff38  RX -
01:24:31  N2 status running [last byte 00]
          TX bd0601ffffffffffbf  RX bd0a81015a000281006400008a  [status on bright=90 speed=0 program program=4 RGBW=0,100,0,0]
01:24:40  [cam] N2 DIY2 blue running [last byte 00]: ratio 0.695 0.697  (luma 98.6, ref 141.4)
01:24:40  N2 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:24:41  N2 status after
          TX bd0601ffffffffffbf  RX bd0a81015a0000810064000088  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### O empty DIY slot selected

```
01:25:23  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:25:23  O0 status before (manual green)
          TX bd0601ffffffffffbf  RX bd0a81015a0000810064000088  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:25:23  O0 query_program(5) (expect empty)
          TX bd060282ffffffff43  RX bd06828200007f81c7  [program header DIY3 count=0]
01:25:24  O0 query_program(5) (expect empty) +listen: first RX bd06828200007f81c7  then 0 more: 
01:25:24  O1 program(5) select empty DIY 3
          TX ad060882ffffffff39  RX -
01:25:24  O1 status
          TX bd0601ffffffffffbf  RX bd0a81015a000282006400008b  [status on bright=90 speed=0 program program=5 RGBW=0,100,0,0]
01:25:33  [cam] O1 empty DIY 3 running: ratio 0.473 0.474  (luma 66.6, ref 140.4)
01:25:33  O2 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:25:34  O2 status
          TX bd0601ffffffffffbf  RX bd0a81015a0000820064000089  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### Q 12-point upload, fast frames, byte-exact readback (DIY 1)

```
01:25:52  verify_key
          TX bd060a78563412ffe0  RX -
01:25:52  Q0 status before
          TX bd0601ffffffffffbf  RX bd0a81015a0000820064000089  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:25:52  ## uploading 13 frames with await_response_ms=0 (gap = HA service round trip)
01:25:52  Q1 upload frame 0
          TX ad0609800c007fffc6  RX -
01:25:52  Q1 upload frame 1
          TX ad080a00001e00000000dd  RX -
01:25:52  Q1 upload frame 2
          TX ad080a0102000a141e2826  RX -
01:25:52  Q1 upload frame 3
          TX ad080a02040f6400000038  RX -
01:25:52  Q1 upload frame 4
          TX ad080a03062d0064000059  RX -
01:25:52  Q1 upload frame 5
          TX ad080a040800000064002f  RX -
01:25:52  Q1 upload frame 6
          TX ad080a050a0a000000643c  RX -
01:25:52  Q1 upload frame 7
          TX ad080a060c006464646461  RX -
01:25:52  Q1 upload frame 8
          TX ad080a070e1432190c0645  RX -
01:25:52  Q1 upload frame 9
          TX ad080a08103b010203041c  RX -
01:25:52  Q1 upload frame 10
          TX ad080a0913056362616066  RX -
01:25:52  Q1 upload frame 11
          TX ad080a0a151e21426300c2  RX -
01:25:52  Q1 upload frame 12
          TX ad080a0b173b000000001c  RX -
01:25:52  ## per-call wall times (ms): 1 1 1 1 1 1 1 1 1 1 1 1 1
01:25:54  Q2 readback 12 points
          TX bd060280ffffffff41  RX -
01:25:54  re-key
          TX bd060a78563412ffe0  RX -
01:25:55  Q2 readback 12 points
          TX bd060280ffffffff41  RX bd0682800c007f82d2  [program header DIY1 count=12]
01:25:56  Q2 readback 12 points +listen: first RX bd0682800c007f82d2  then 12 more: bd088300001e0000000066@6ms bd08830102000a141e28af@9ms bd088302040f64000000c1@11ms bd088303062d00640000e2@88ms bd088304080000006400b8@108ms bd0883050000000000004d@134ms bd0883060c0064646464ea@230ms bd0883070e1432190c06ce@231ms bd088308103b01020304a5@233ms bd08830900000000000051@304ms bd08830a00000000000052@308ms bd08830b00000000000053@310ms
01:25:56  ## readback exact match
```

### R inter-frame gap vs frame loss (12-point upload, DIY 1)

```
01:26:33  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:26:33  R upload T=10
          TX ad0609800c007fffc6  RX -
01:26:33  R upload T=10
          TX ad080a0002093f04323877  RX -
01:26:33  R upload T=10
          TX ad080a01030c4e626301e3  RX -
01:26:33  R upload T=10
          TX ad080a0204015a3a235dda  RX -
01:26:33  R upload T=10
          TX ad080a0304231e4c0e298a  RX -
01:26:33  R upload T=10
          TX ad080a0407090403045432  RX -
01:26:33  R upload T=10
          TX ad080a05082a46023158c7  RX -
01:26:33  R upload T=10
          TX ad080a060c391c375d04be  RX -
01:26:33  R upload T=10
          TX ad080a070f14441d6239e5  RX -
01:26:33  R upload T=10
          TX ad080a08100740471e2db0  RX -
01:26:33  R upload T=10
          TX ad080a0910361e571d6202  RX -
01:26:33  R upload T=10
          TX ad080a0a13193b2603368f  RX -
01:26:33  R upload T=10
          TX ad080a0b160e48530d18ae  RX -
01:26:34  R readback T=10
          TX bd060280ffffffff41  RX -
01:26:35  re-key
          TX bd060a78563412ffe0  RX -
01:26:35  R readback T=10
          TX bd060280ffffffff41  RX bd0682800c007fff4f  [program header DIY1 count=12]
01:26:37  R readback T=10 +listen: first RX bd0682800c007fff4f  then 12 more: bd08830002093f04323800@7ms bd088301030c4e6263016c@113ms bd08830204015a3a235d63@114ms bd08830304231e4c0e2913@116ms bd088304070904030454bb@118ms bd088305082a4602315850@200ms bd0883060c391c375d0447@229ms bd0883070f14441d62396e@230ms bd088308100740471e2d39@244ms bd08830910361e571d628b@246ms bd08830a13193b26033618@315ms bd08830b00000000000053@319ms
01:26:37  ## T= 10 ms  actual avg period 11 ms/frame  exact=False  mismatched points=1
01:26:37  R upload T=20
          TX ad0609800c007fffc6  RX -
01:26:37  R upload T=20
          TX ad080a00010d4b581538bd  RX -
01:26:37  R upload T=20
          TX ad080a01013752335d421c  RX -
01:26:37  R upload T=20
          TX ad080a02023530463941e8  RX -
01:26:37  R upload T=20
          TX ad080a0303072305042f27  RX -
01:26:37  R upload T=20
          TX ad080a04052e3c293137c3  RX -
01:26:37  R upload T=20
          TX ad080a05070e4416481792  RX -
01:26:37  R upload T=20
          TX ad080a0608231f1e041748  RX -
01:26:37  R upload T=20
          TX ad080a070a1f2a17124284  RX -
01:26:37  R upload T=20
          TX ad080a080c13422f4257f0  RX -
01:26:37  R upload T=20
          TX ad080a09142848183a36d4  RX -
01:26:37  R upload T=20
          TX ad080a0a142a5f44622f3b  RX -
01:26:37  R upload T=20
          TX ad080a0b16334c2e2f3af6  RX -
01:26:37  R readback T=20
          TX bd060280ffffffff41  RX bd0682800c007f0050  [program header DIY1 count=12]
01:26:38  R readback T=20 +listen: first RX bd0682800c007f0050  then 12 more: bd088300010d4b58153846@9ms bd088301013752335d42a5@12ms bd08830202353046394171@13ms bd08830303072305042fb0@46ms bd088304052e3c2931374c@47ms bd088305070e441648171b@116ms bd08830608231f1e0417d1@137ms bd0883070a1f2a1712420d@144ms bd0883080c13422f425779@205ms bd088309142848183a365d@212ms bd08830a142a5f44622fc4@327ms bd08830b16334c2e2f3a7f@329ms
01:26:38  ## T= 20 ms  actual avg period 21 ms/frame  exact=True  mismatched points=0
01:26:38  R upload T=40
          TX ad0609800c007fffc6  RX -
01:26:38  R upload T=40
          TX ad080a00001a3d22471e9d  RX -
01:26:38  R upload T=40
          TX ad080a01020e195c3d46c8  RX -
01:26:38  R upload T=40
          TX ad080a02041b473d3352e9  RX -
01:26:38  R upload T=40
          TX ad080a030807141e521469  RX -
01:26:38  R upload T=40
          TX ad080a040c2543325f02ca  RX -
01:26:38  R upload T=40
          TX ad080a05100a56640915b6  RX -
01:26:38  R upload T=40
          TX ad080a061222624c0627d4  RX -
01:26:39  R upload T=40
          TX ad080a0713316404233dd2  RX -
01:26:39  R upload T=40
          TX ad080a08140d4d5d325c20  RX -
01:26:39  R upload T=40
          TX ad080a09142437335e4a12  RX -
01:26:39  R upload T=40
          TX ad080a0a142839122f0d8c  RX -
01:26:39  R upload T=40
          TX ad080a0b15150512401c67  RX -
01:26:39  R readback T=40
          TX bd060280ffffffff41  RX bd0682800c007f2e7e  [program header DIY1 count=12]
01:26:40  R readback T=40 +listen: first RX bd0682800c007f2e7e  then 12 more: bd088300001a3d22471e26@3ms bd088301020e195c3d4651@9ms bd088302041b473d335272@14ms bd0883030807141e5214f2@66ms bd0883040c2543325f0253@84ms bd088305100a566409153f@180ms bd0883061222624c06275d@182ms bd08830713316404233d5b@183ms bd088308140d4d5d325ca9@209ms bd088309142437335e4a9b@214ms bd08830a142839122f0d15@282ms bd08830b15150512401cf0@286ms
01:26:40  ## T= 40 ms  actual avg period 41 ms/frame  exact=True  mismatched points=0
01:26:40  R upload T=60
          TX ad0609800c007fffc6  RX -
01:26:40  R upload T=60
          TX ad080a00002862081d43b1  RX -
01:26:40  R upload T=60
          TX ad080a010210452f2464ce  RX -
01:26:40  R upload T=60
          TX ad080a020304170e221c2b  RX -
01:26:40  R upload T=60
          TX ad080a03031f0453222380  RX -
01:26:40  R upload T=60
          TX ad080a0405111916282656  RX -
01:26:40  R upload T=60
          TX ad080a050803515e300cba  RX -
01:26:41  R upload T=60
          TX ad080a0609344e2c563204  RX -
01:26:41  R upload T=60
          TX ad080a070a15412017207d  RX -
01:26:41  R upload T=60
          TX ad080a080d1f3d240c47a7  RX -
01:26:41  R upload T=60
          TX ad080a090d2a2701264a97  RX -
01:26:41  R upload T=60
          TX ad080a0a10145b28624214  RX -
01:26:41  R upload T=60
          TX ad080a0b122d1935374ddb  RX -
01:26:41  R readback T=60
          TX bd060280ffffffff41  RX bd0682800c007f1262  [program header DIY1 count=12]
01:26:42  R readback T=60 +listen: first RX bd0682800c007f1262  then 12 more: bd088300002862081d433a@9ms bd0883010210452f246457@29ms bd0883020304170e221cb4@33ms bd088303031f0453222309@96ms bd088304051119162826df@98ms bd0883050803515e300c43@212ms bd08830609344e2c56328d@214ms bd0883070a154120172006@220ms bd0883080d1f3d240c4730@239ms bd0883090d2a2701264a20@318ms bd08830a10145b2862429d@320ms bd08830b122d1935374d64@327ms
01:26:42  ## T= 60 ms  actual avg period 61 ms/frame  exact=True  mismatched points=0
01:26:42  R upload T=100
          TX ad0609800c007fffc6  RX -
01:26:42  R upload T=100
          TX ad080a00003b0f303d2096  RX -
01:26:42  R upload T=100
          TX ad080a01012e31460e4abe  RX -
01:26:43  R upload T=100
          TX ad080a02051520025e1c77  RX -
01:26:43  R upload T=100
          TX ad080a03081e35241863bc  RX -
01:26:43  R upload T=100
          TX ad080a04082b3215620aa9  RX -
01:26:43  R upload T=100
          TX ad080a050c0e12505039c9  RX -
01:26:43  R upload T=100
          TX ad080a060f35111101012d  RX -
01:26:43  R upload T=100
          TX ad080a0712051b641c168e  RX -
01:26:43  R upload T=100
          TX ad080a08150f1626291a6a  RX -
01:26:43  R upload T=100
          TX ad080a0916094657511bf0  RX -
01:26:43  R upload T=100
          TX ad080a0a160f18591a32ab  RX -
01:26:43  R upload T=100
          TX ad080a0b172227032f3692  RX -
01:26:44  R readback T=100
          TX bd060280ffffffff41  RX bd0682800c007f3585  [program header DIY1 count=12]
01:26:45  R readback T=100 +listen: first RX bd0682800c007f3585  then 12 more: bd088300003b0f303d201f@1ms bd088301012e31460e4a47@3ms bd088302051520025e1c00@111ms bd088303081e3524186345@112ms bd088304082b3215620a32@120ms bd0883050c0e1250503952@130ms bd0883060f3511110101b6@131ms bd08830712051b641c1617@206ms bd088308150f1626291af3@208ms bd08830916094657511b79@307ms bd08830a160f18591a3234@308ms bd08830b172227032f361b@311ms
01:26:45  ## T=100 ms  actual avg period 101 ms/frame  exact=True  mismatched points=0
```

### R2 gap reliability (12-point upload, DIY 1)

```
01:26:58  verify_key
          TX bd060a78563412ffe0  RX -
01:26:58  R2 upload T=15
          TX ad0609800c007fffc6  RX -
01:26:58  R2 upload T=15
          TX ad080a00011422373c53bc  RX -
01:26:58  R2 upload T=15
          TX ad080a010213464a2853e0  RX -
01:26:58  R2 upload T=15
          TX ad080a0205154a601557f1  RX -
01:26:58  R2 upload T=15
          TX ad080a03052817375719ad  RX -
01:26:58  R2 upload T=15
          TX ad080a04080d3d241c4a9f  RX -
01:26:58  R2 upload T=15
          TX ad080a050c2e5f373b5f2e  RX -
01:26:58  R2 upload T=15
          TX ad080a060d0d561f3255db  RX -
01:26:58  R2 upload T=15
          TX ad080a070d2a4b3d2c4f00  RX -
01:26:58  R2 upload T=15
          TX ad080a080d391c192a3ba7  RX -
01:26:58  R2 upload T=15
          TX ad080a0913052b602e64fd  RX -
01:26:58  R2 upload T=15
          TX ad080a0a130b1c20070d37  RX -
01:26:58  R2 upload T=15
          TX ad080a0b16374106423ddd  RX -
01:26:58  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f0353  [program header DIY1 count=12]
01:26:59  ## T= 15 ms seed=250 period 16 ms/frame exact=False mismatched=3
01:26:59  R2 upload T=15
          TX ad0609800c007fffc6  RX -
01:26:59  R2 upload T=15
          TX ad080a00030b3b341f429d  RX -
01:26:59  R2 upload T=15
          TX ad080a0106364f5f1528e7  RX -
01:26:59  R2 upload T=15
          TX ad080a0208194d5d0f39d4  RX -
01:27:00  R2 upload T=15
          TX ad080a0308392d1448018d  RX -
01:27:00  R2 upload T=15
          TX ad080a040d03203f614fe2  RX -
01:27:00  R2 upload T=15
          TX ad080a050e001b0b4d195e  RX -
01:27:00  R2 upload T=15
          TX ad080a060f0a36160a1a4e  RX -
01:27:00  R2 upload T=15
          TX ad080a070f235c59175519  RX -
01:27:00  R2 upload T=15
          TX ad080a0810290f3c030a58  RX -
01:27:00  R2 upload T=15
          TX ad080a09132b5362624562  RX -
01:27:00  R2 upload T=15
          TX ad080a0a1401194f424ed6  RX -
01:27:00  R2 upload T=15
          TX ad080a0b161f051c5f27a6  RX -
01:27:00  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f0353  [program header DIY1 count=12]
01:27:01  ## T= 15 ms seed=251 period 16 ms/frame exact=True mismatched=0
01:27:01  R2 upload T=15
          TX ad0609800c007fffc6  RX -
01:27:01  R2 upload T=15
          TX ad080a00002103645e0eb3  RX -
01:27:01  R2 upload T=15
          TX ad080a01043a634d225424  RX -
01:27:01  R2 upload T=15
          TX ad080a02062540615e0bf6  RX -
01:27:01  R2 upload T=15
          TX ad080a03083b5e250624b2  RX -
01:27:01  R2 upload T=15
          TX ad080a0409211f5b235ce6  RX -
01:27:01  R2 upload T=15
          TX ad080a050939514c0311b7  RX -
01:27:01  R2 upload T=15
          TX ad080a060b2130554123da  RX -
01:27:01  R2 upload T=15
          TX ad080a070f210b595441ef  RX -
01:27:01  R2 upload T=15
          TX ad080a08102f1f55280daf  RX -
01:27:01  R2 upload T=15
          TX ad080a09121b0a381c0659  RX -
01:27:01  R2 upload T=15
          TX ad080a0a150d084612549f  RX -
01:27:01  R2 upload T=15
          TX ad080a0b171a03602839bf  RX -
01:27:01  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f1c6c  [program header DIY1 count=12]
01:27:03  ## T= 15 ms seed=252 period 16 ms/frame exact=False mismatched=1
01:27:03  R2 upload T=15
          TX ad0609800c007fffc6  RX -
01:27:03  R2 upload T=15
          TX ad080a00020824402a5db4  RX -
01:27:03  R2 upload T=15
          TX ad080a010209475d03299b  RX -
01:27:03  R2 upload T=15
          TX ad080a0204381e1b063672  RX -
01:27:03  R2 upload T=15
          TX ad080a03043a5434223ee8  RX -
01:27:03  R2 upload T=15
          TX ad080a04072d400b5425bb  RX -
01:27:03  R2 upload T=15
          TX ad080a050d015d21581ac2  RX -
01:27:03  R2 upload T=15
          TX ad080a060d353c524b5e3e  RX -
01:27:03  R2 upload T=15
          TX ad080a070f0d1519442377  RX -
01:27:03  R2 upload T=15
          TX ad080a081209301702315c  RX -
01:27:03  R2 upload T=15
          TX ad080a0912162534524ae5  RX -
01:27:03  R2 upload T=15
          TX ad080a0a16104d026134d3  RX -
01:27:03  R2 upload T=15
          TX ad080a0b161730554d15de  RX -
01:27:03  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f60b0  [program header DIY1 count=12]
01:27:04  ## T= 15 ms seed=253 period 16 ms/frame exact=True mismatched=0
01:27:04  R2 upload T=20
          TX ad0609800c007fffc6  RX -
01:27:04  R2 upload T=20
          TX ad080a00000310372b3064  RX -
01:27:04  R2 upload T=20
          TX ad080a01051f4a125120b1  RX -
01:27:04  R2 upload T=20
          TX ad080a0205270d4d4164ec  RX -
01:27:04  R2 upload T=20
          TX ad080a030b2d42630759ff  RX -
01:27:04  R2 upload T=20
          TX ad080a040b3109262652a6  RX -
01:27:04  R2 upload T=20
          TX ad080a050c09480a422491  RX -
01:27:04  R2 upload T=20
          TX ad080a060e1b4a2d030971  RX -
01:27:04  R2 upload T=20
          TX ad080a07102c0c501b31aa  RX -
01:27:04  R2 upload T=20
          TX ad080a08120327646105cd  RX -
01:27:04  R2 upload T=20
          TX ad080a09131f4c17075ec2  RX -
01:27:04  R2 upload T=20
          TX ad080a0a132f3e390750d9  RX -
01:27:04  R2 upload T=20
          TX ad080a0b141823571a58e2  RX -
01:27:05  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f55a5  [program header DIY1 count=12]
01:27:06  ## T= 20 ms seed=300 period 21 ms/frame exact=True mismatched=0
01:27:06  R2 upload T=20
          TX ad0609800c007fffc6  RX -
01:27:06  R2 upload T=20
          TX ad080a00040343110d3a61  RX -
01:27:06  R2 upload T=20
          TX ad080a010803475b5128e6  RX -
01:27:06  R2 upload T=20
          TX ad080a02081205525660e8  RX -
01:27:06  R2 upload T=20
          TX ad080a03082917254829a0  RX -
01:27:06  R2 upload T=20
          TX ad080a040d243d1d505ffd  RX -
01:27:06  R2 upload T=20
          TX ad080a050f28633b0363ff  RX -
01:27:06  R2 upload T=20
          TX ad080a061032030a3a2573  RX -
01:27:06  R2 upload T=20
          TX ad080a07121d192c1562b1  RX -
01:27:06  R2 upload T=20
          TX ad080a08150c494d4e04d0  RX -
01:27:06  R2 upload T=20
          TX ad080a0915165029050a7b  RX -
01:27:06  R2 upload T=20
          TX ad080a0a160e2d16320c6e  RX -
01:27:06  R2 upload T=20
          TX ad080a0b1630053a5d25d1  RX -
01:27:06  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f57a7  [program header DIY1 count=12]
01:27:08  ## T= 20 ms seed=301 period 21 ms/frame exact=True mismatched=0
01:27:08  R2 upload T=20
          TX ad0609800c007fffc6  RX -
01:27:08  R2 upload T=20
          TX ad080a0001102c63530fc1  RX -
01:27:08  R2 upload T=20
          TX ad080a01061b55554604d5  RX -
01:27:08  R2 upload T=20
          TX ad080a02062d521d4b11bf  RX -
01:27:08  R2 upload T=20
          TX ad080a030c3641075747ea  RX -
01:27:08  R2 upload T=20
          TX ad080a040d1c0a2f6231b8  RX -
01:27:08  R2 upload T=20
          TX ad080a050d223d26455bf6  RX -
01:27:08  R2 upload T=20
          TX ad080a060d353931625427  RX -
01:27:08  R2 upload T=20
          TX ad080a07100c265e120c84  RX -
01:27:08  R2 upload T=20
          TX ad080a08110b0862271a8e  RX -
01:27:08  R2 upload T=20
          TX ad080a0912173626631fcf  RX -
01:27:08  R2 upload T=20
          TX ad080a0a12352546623815  RX -
01:27:08  R2 upload T=20
          TX ad080a0b13015120120465  RX -
01:27:08  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f3a8a  [program header DIY1 count=12]
01:27:09  ## T= 20 ms seed=302 period 21 ms/frame exact=True mismatched=0
01:27:09  R2 upload T=20
          TX ad0609800c007fffc6  RX -
01:27:09  R2 upload T=20
          TX ad080a0001043d4b0c2179  RX -
01:27:09  R2 upload T=20
          TX ad080a0103084446635810  RX -
01:27:09  R2 upload T=20
          TX ad080a020516054d105997  RX -
01:27:09  R2 upload T=20
          TX ad080a030627331240269a  RX -
01:27:09  R2 upload T=20
          TX ad080a04080b01505735b3  RX -
01:27:09  R2 upload T=20
          TX ad080a050831245b051e9f  RX -
01:27:09  R2 upload T=20
          TX ad080a060d231c2c141566  RX -
01:27:09  R2 upload T=20
          TX ad080a070d3b1b4c260ea9  RX -
01:27:09  R2 upload T=20
          TX ad080a0811255a205c5a2d  RX -
01:27:09  R2 upload T=20
          TX ad080a09131344542144eb  RX -
01:27:09  R2 upload T=20
          TX ad080a0a1314205e032394  RX -
01:27:10  R2 upload T=20
          TX ad080a0b1510612459521f  RX -
01:27:10  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f2070  [program header DIY1 count=12]
01:27:11  ## T= 20 ms seed=303 period 21 ms/frame exact=True mismatched=0
01:27:11  R2 upload T=30
          TX ad0609800c007fffc6  RX -
01:27:11  R2 upload T=30
          TX ad080a00030a3a1c5538af  RX -
01:27:11  R2 upload T=30
          TX ad080a010827135d062085  RX -
01:27:11  R2 upload T=30
          TX ad080a02082d50630132dc  RX -
01:27:11  R2 upload T=30
          TX ad080a03092b28501e35c1  RX -
01:27:11  R2 upload T=30
          TX ad080a040a1e1d0e0d0c2f  RX -
01:27:11  R2 upload T=30
          TX ad080a050e022a094d5baf  RX -
01:27:11  R2 upload T=30
          TX ad080a060e190a633649d8  RX -
01:27:11  R2 upload T=30
          TX ad080a070f26455204089e  RX -
01:27:11  R2 upload T=30
          TX ad080a081029080d1e1144  RX -
01:27:11  R2 upload T=30
          TX ad080a09122847290a50cc  RX -
01:27:11  R2 upload T=30
          TX ad080a0a130a262a343fa9  RX -
01:27:11  R2 upload T=30
          TX ad080a0b1313041f0f3759  RX -
01:27:11  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f2474  [program header DIY1 count=12]
01:27:13  ## T= 30 ms seed=400 period 31 ms/frame exact=True mismatched=0
01:27:13  R2 upload T=30
          TX ad0609800c007fffc6  RX -
01:27:13  R2 upload T=30
          TX ad080a00031b18105c57b8  RX -
01:27:13  R2 upload T=30
          TX ad080a010617540a6122be  RX -
01:27:13  R2 upload T=30
          TX ad080a020921570e445bef  RX -
01:27:13  R2 upload T=30
          TX ad080a030a1e02554060e1  RX -
01:27:13  R2 upload T=30
          TX ad080a040a360a2702568c  RX -
01:27:13  R2 upload T=30
          TX ad080a050d170d15254372  RX -
01:27:13  R2 upload T=30
          TX ad080a060e231e443416a2  RX -
01:27:13  R2 upload T=30
          TX ad080a070e3a3c2e3255ff  RX -
01:27:13  R2 upload T=30
          TX ad080a08101b230333145f  RX -
01:27:13  R2 upload T=30
          TX ad080a09103b4d301550f5  RX -
01:27:13  R2 upload T=30
          TX ad080a0a131c0d613c20c2  RX -
01:27:13  R2 upload T=30
          TX ad080a0b16322b232827af  RX -
01:27:13  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f1f6f  [program header DIY1 count=12]
01:27:14  ## T= 30 ms seed=401 period 31 ms/frame exact=True mismatched=0
01:27:14  R2 upload T=30
          TX ad0609800c007fffc6  RX -
01:27:14  R2 upload T=30
          TX ad080a00031358525e16f3  RX -
01:27:14  R2 upload T=30
          TX ad080a010a1301633d50ce  RX -
01:27:14  R2 upload T=30
          TX ad080a020d323e165917c4  RX -
01:27:15  R2 upload T=30
          TX ad080a030d396259223f24  RX -
01:27:15  R2 upload T=30
          TX ad080a040e0f0d14634db1  RX -
01:27:15  R2 upload T=30
          TX ad080a050f213e01145198  RX -
01:27:15  R2 upload T=30
          TX ad080a06102a26392257d7  RX -
01:27:15  R2 upload T=30
          TX ad080a07122d30444b10d4  RX -
01:27:15  R2 upload T=30
          TX ad080a08130f4e600349e3  RX -
01:27:15  R2 upload T=30
          TX ad080a09151e29255845e6  RX -
01:27:15  R2 upload T=30
          TX ad080a0a15372f481d27d0  RX -
01:27:15  R2 upload T=30
          TX ad080a0b17285450561b1e  RX -
01:27:15  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f2373  [program header DIY1 count=12]
01:27:16  ## T= 30 ms seed=402 period 31 ms/frame exact=True mismatched=0
01:27:16  R2 upload T=30
          TX ad0609800c007fffc6  RX -
01:27:16  R2 upload T=30
          TX ad080a00010c3314590f7b  RX -
01:27:16  R2 upload T=30
          TX ad080a0105065422141368  RX -
01:27:16  R2 upload T=30
          TX ad080a02051d143b151960  RX -
01:27:16  R2 upload T=30
          TX ad080a03081f614f4b6145  RX -
01:27:16  R2 upload T=30
          TX ad080a0409085f383c03aa  RX -
01:27:16  R2 upload T=30
          TX ad080a050b283d6242370f  RX -
01:27:16  R2 upload T=30
          TX ad080a060c1e601d014cb9  RX -
01:27:16  R2 upload T=30
          TX ad080a070c3457155c5e2c  RX -
01:27:16  R2 upload T=30
          TX ad080a0810112d26082d70  RX -
01:27:16  R2 upload T=30
          TX ad080a091202462f4d4cea  RX -
01:27:16  R2 upload T=30
          TX ad080a0a12355544571010  RX -
01:27:17  R2 upload T=30
          TX ad080a0b15122c425335e7  RX -
01:27:17  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f50a0  [program header DIY1 count=12]
01:27:18  ## T= 30 ms seed=403 period 31 ms/frame exact=True mismatched=0
01:27:18  R2 upload T=40
          TX ad0609800c007fffc6  RX -
01:27:18  R2 upload T=40
          TX ad080a0003295f0c031a73  RX -
01:27:18  R2 upload T=40
          TX ad080a01070f2b1e332678  RX -
01:27:18  R2 upload T=40
          TX ad080a02082b0761092a8f  RX -
01:27:18  R2 upload T=40
          TX ad080a030b223a502830d1  RX -
01:27:18  R2 upload T=40
          TX ad080a040c31123f0f1979  RX -
01:27:18  R2 upload T=40
          TX ad080a050f2d30451530ba  RX -
01:27:18  R2 upload T=40
          TX ad080a060f3b2f2b3918ba  RX -
01:27:18  R2 upload T=40
          TX ad080a07113a440b4e01af  RX -
01:27:18  R2 upload T=40
          TX ad080a08131d13633c40e9  RX -
01:27:18  R2 upload T=40
          TX ad080a09132c5d18634d2c  RX -
01:27:18  R2 upload T=40
          TX ad080a0a15293e553a471b  RX -
01:27:18  R2 upload T=40
          TX ad080a0b17303317534af8  RX -
01:27:18  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f4292  [program header DIY1 count=12]
01:27:20  ## T= 40 ms seed=500 period 41 ms/frame exact=True mismatched=0
01:27:20  R2 upload T=40
          TX ad0609800c007fffc6  RX -
01:27:20  R2 upload T=40
          TX ad080a0005333a5d3b02cb  RX -
01:27:20  R2 upload T=40
          TX ad080a01080d303c0361a5  RX -
01:27:20  R2 upload T=40
          TX ad080a020927301c123b8a  RX -
01:27:20  R2 upload T=40
          TX ad080a030b0b6457013cd0  RX -
01:27:20  R2 upload T=40
          TX ad080a040b1d1219333079  RX -
01:27:20  R2 upload T=40
          TX ad080a050b272e0e04558b  RX -
01:27:20  R2 upload T=40
          TX ad080a060c00101c4a64ab  RX -
01:27:20  R2 upload T=40
          TX ad080a070c040f1440245d  RX -
01:27:20  R2 upload T=40
          TX ad080a080c19144b6005b0  RX -
01:27:20  R2 upload T=40
          TX ad080a0912061964042c8d  RX -
01:27:20  R2 upload T=40
          TX ad080a0a151b313c4d16c9  RX -
01:27:20  R2 upload T=40
          TX ad080a0b153136335d0ae0  RX -
01:27:20  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f1767  [program header DIY1 count=12]
01:27:22  ## T= 40 ms seed=501 period 41 ms/frame exact=True mismatched=0
01:27:22  R2 upload T=40
          TX ad0609800c007fffc6  RX -
01:27:22  R2 upload T=40
          TX ad080a0001170a20291a44  RX -
01:27:22  R2 upload T=40
          TX ad080a01052d53630e550b  RX -
01:27:22  R2 upload T=40
          TX ad080a02052f2a63594520  RX -
01:27:22  R2 upload T=40
          TX ad080a030c001e5c204ab2  RX -
01:27:22  R2 upload T=40
          TX ad080a040c18095105094f  RX -
01:27:22  R2 upload T=40
          TX ad080a050c380c4f5a17d4  RX -
01:27:22  R2 upload T=40
          TX ad080a060e211a44054ca3  RX -
01:27:22  R2 upload T=40
          TX ad080a07110f07390d1f52  RX -
01:27:22  R2 upload T=40
          TX ad080a081110114120035d  RX -
01:27:22  R2 upload T=40
          TX ad080a09151e611607148d  RX -
01:27:22  R2 upload T=40
          TX ad080a0a153b4523454b11  RX -
01:27:22  R2 upload T=40
          TX ad080a0b17310d213250c2  RX -
01:27:22  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f3383  [program header DIY1 count=12]
01:27:23  ## T= 40 ms seed=502 period 41 ms/frame exact=True mismatched=0
01:27:23  R2 upload T=40
          TX ad0609800c007fffc6  RX -
01:27:24  R2 upload T=40
          TX ad080a000026100405605e  RX -
01:27:24  R2 upload T=40
          TX ad080a010118485f3c36f2  RX -
01:27:24  R2 upload T=40
          TX ad080a0204295e050c62bf  RX -
01:27:24  R2 upload T=40
          TX ad080a03050a53433061f8  RX -
01:27:24  R2 upload T=40
          TX ad080a0405323c4d363df6  RX -
01:27:24  R2 upload T=40
          TX ad080a0506215c243653f4  RX -
01:27:24  R2 upload T=40
          TX ad080a060a24255c3f12c5  RX -
01:27:24  R2 upload T=40
          TX ad080a070c04222b0a3865  RX -
01:27:24  R2 upload T=40
          TX ad080a080c351d443813b4  RX -
01:27:24  R2 upload T=40
          TX ad080a090e1809031a1d31  RX -
01:27:24  R2 upload T=40
          TX ad080a0a1605592f3e26d0  RX -
01:27:24  R2 upload T=40
          TX ad080a0b17341445305ffd  RX -
01:27:24  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f2171  [program header DIY1 count=12]
01:27:25  ## T= 40 ms seed=503 period 40 ms/frame exact=True mismatched=0
01:27:25  R2 upload T=80
          TX ad0609800c007fffc6  RX -
01:27:25  R2 upload T=80
          TX ad080a00012f1d473744ce  RX -
01:27:25  R2 upload T=80
          TX ad080a01023225315c1fc5  RX -
01:27:26  R2 upload T=80
          TX ad080a02030f490f3b44aa  RX -
01:27:26  R2 upload T=80
          TX ad080a0305212b4b2d0792  RX -
01:27:26  R2 upload T=80
          TX ad080a04072f44581b0bbb  RX -
01:27:26  R2 upload T=80
          TX ad080a05081348423e18bf  RX -
01:27:26  R2 upload T=80
          TX ad080a060b112019101a44  RX -
01:27:26  R2 upload T=80
          TX ad080a070c2f392f2d32c8  RX -
01:27:26  R2 upload T=80
          TX ad080a080f08444e0f3bba  RX -
01:27:26  R2 upload T=80
          TX ad080a09151259520e2bd3  RX -
01:27:26  R2 upload T=80
          TX ad080a0a1621383f042ca7  RX -
01:27:26  R2 upload T=80
          TX ad080a0b172e61074e07cc  RX -
01:27:26  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f4595  [program header DIY1 count=12]
01:27:28  ## T= 80 ms seed=900 period 81 ms/frame exact=True mismatched=0
01:27:28  R2 upload T=80
          TX ad0609800c007fffc6  RX -
01:27:28  R2 upload T=80
          TX ad080a0003363f325735f5  RX -
01:27:28  R2 upload T=80
          TX ad080a0105133c544e49ff  RX -
01:27:28  R2 upload T=80
          TX ad080a0209055909410f81  RX -
01:27:28  R2 upload T=80
          TX ad080a030a032731580e8d  RX -
01:27:28  R2 upload T=80
          TX ad080a040a385b2e4f0eeb  RX -
01:27:28  R2 upload T=80
          TX ad080a050d0a56033547b0  RX -
01:27:28  R2 upload T=80
          TX ad080a060e2b3e45582700  RX -
01:27:28  R2 upload T=80
          TX ad080a0710235c27385e12  RX -
01:27:28  R2 upload T=80
          TX ad080a08133631491601a1  RX -
01:27:28  R2 upload T=80
          TX ad080a0915302a4e0652dd  RX -
01:27:29  R2 upload T=80
          TX ad080a0a161f22531e0e9f  RX -
01:27:29  R2 upload T=80
          TX ad080a0b1714375e300bc5  RX -
01:27:29  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f0757  [program header DIY1 count=12]
01:27:30  ## T= 80 ms seed=901 period 81 ms/frame exact=True mismatched=0
01:27:30  R2 upload T=80
          TX ad0609800c007fffc6  RX -
01:27:30  R2 upload T=80
          TX ad080a00020f1a43144283  RX -
01:27:30  R2 upload T=80
          TX ad080a01043727622c4dfd  RX -
01:27:30  R2 upload T=80
          TX ad080a0209134d583c23e1  RX -
01:27:30  R2 upload T=80
          TX ad080a030d2842551616ba  RX -
01:27:30  R2 upload T=80
          TX ad080a0410184e275d611e  RX -
01:27:31  R2 upload T=80
          TX ad080a05120938232c1b81  RX -
01:27:31  R2 upload T=80
          TX ad080a06131a3828180670  RX -
01:27:31  R2 upload T=80
          TX ad080a071407083b401579  RX -
01:27:31  R2 upload T=80
          TX ad080a081527583b3626f2  RX -
01:27:31  R2 upload T=80
          TX ad080a09153a4f4e134a11  RX -
01:27:31  R2 upload T=80
          TX ad080a0a1604132e531a91  RX -
01:27:31  R2 upload T=80
          TX ad080a0b170c343322067c  RX -
01:27:31  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f5eae  [program header DIY1 count=12]
01:27:32  ## T= 80 ms seed=902 period 81 ms/frame exact=True mismatched=0
01:27:32  R2 upload T=80
          TX ad0609800c007fffc6  RX -
01:27:32  R2 upload T=80
          TX ad080a00011a0b4b202171  RX -
01:27:33  R2 upload T=80
          TX ad080a0102303c532f0fbf  RX -
01:27:33  R2 upload T=80
          TX ad080a0206110c305f40b3  RX -
01:27:33  R2 upload T=80
          TX ad080a03061c272662089b  RX -
01:27:33  R2 upload T=80
          TX ad080a040624230a51339e  RX -
01:27:33  R2 upload T=80
          TX ad080a0509363d185345f0  RX -
01:27:33  R2 upload T=80
          TX ad080a060d1e435d336225  RX -
01:27:33  R2 upload T=80
          TX ad080a070d281c12622db8  RX -
01:27:33  R2 upload T=80
          TX ad080a080f0c5b1c3747d7  RX -
01:27:33  R2 upload T=80
          TX ad080a09130960382b1fc6  RX -
01:27:33  R2 upload T=80
          TX ad080a0a16111405335894  RX -
01:27:33  R2 upload T=80
          TX ad080a0b163824212838bd  RX -
01:27:34  R2 query
          TX bd060280ffffffff41  RX bd0682800c007f3383  [program header DIY1 count=12]
01:27:35  ## T= 80 ms seed=903 period 81 ms/frame exact=True mismatched=0
01:27:35  ## summary {15: '2/4', 20: '4/4', 30: '4/4', 40: '4/4', 80: '4/4'}
```

### T demo (camera)

```
01:27:50  verify_key
          TX bd060a78563412ffe0  RX -
01:27:50  T0 status before (manual green)
          TX bd0601ffffffffffbf  RX bd0a81015a0000820064000089  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:27:52  [cam] T0 baseline manual green: ratio 0.965  (luma 135.3, ref 140.2)
01:27:52  ## demo() from MANUAL mode, watched 9 s
01:27:52  T1 demo() manual
          TX ad060bffffffffffb9  RX -
01:28:03  [cam] camera series 'demo from manual (green baseline 0.96)' (10 samples, changes >= 0.012 shown): 0s=0.965 8.1s=0.964
01:28:03  T1 status after
          TX bd0601ffffffffffbf  RX bd0a81015a0000820064000089  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:28:03  ## demo() from PROGRAM 0 (Japanese, dark at night), watched 12 s
01:28:03  T2 program(0)
          TX ad060800ffffffffb7  RX -
01:28:08  [cam] T2 program 0 baseline (dark): ratio 0.473  (luma 67.0, ref 141.6)
01:28:08  T2 demo() program
          TX ad060bffffffffffb9  RX -
01:28:21  [cam] camera series 'demo from program 0 (dark baseline 0.47)' (14 samples, changes >= 0.012 shown): 0s=0.473 8.1s=1.410 11.7s=1.408
01:28:22  T2 status after demo 12 s
          TX bd0601ffffffffffbf  RX bd0a81015a0002000064000009  [status on bright=90 speed=0 program program=0 RGBW=0,100,0,0]
01:28:27  [cam] T2 program 0 after demo +4s: ratio 1.342  (luma 186.0, ref 138.6)
01:28:27  T3 color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:28:27  T3 status
          TX bd0601ffffffffffbf  RX bd0a81015a0000000064000007  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### U demo duration with a dim DIY program (slot DIY 1)

```
01:29:16  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:29:16  U upload dim demo program frame 0
          TX ad06098007007fffc1  RX -
01:29:16  U upload dim demo program frame 1
          TX ad080a00060000000000c5  RX -
01:29:16  U upload dim demo program frame 2
          TX ad080a01060100001e00e5  RX -
01:29:17  U upload dim demo program frame 3
          TX ad080a020c0000001e00eb  RX -
01:29:17  U upload dim demo program frame 4
          TX ad080a030c01001e0000ed  RX -
01:29:17  U upload dim demo program frame 5
          TX ad080a041200001e0000f3  RX -
01:29:17  U upload dim demo program frame 6
          TX ad080a05120100000000d7  RX -
01:29:17  U upload dim demo program frame 7
          TX ad080a06173b0000000017  RX -
01:29:17  U readback
          TX bd060280ffffffff41  RX bd06828007007fff4a  [program header DIY1 count=7]
01:29:18  U readback +listen: first RX bd06828007007fff4a  then 7 more: bd0883000600000000004e@0ms bd088301060100001e006e@23ms bd0883020c0000001e0074@31ms bd0883030c01001e000076@90ms bd0883041200001e00007c@92ms bd08830512010000000060@200ms bd088306173b00000000a0@200ms
01:29:18  U program(3) select DIY 1
          TX ad060880ffffffff37  RX -
01:29:19  U status running DIY 1
          TX bd0601ffffffffffbf  RX bd0a81015a0002800064000089  [status on bright=90 speed=0 program program=3 RGBW=0,100,0,0]
01:29:23  [cam] U DIY1 baseline (dark expected at this hour): ratio 0.473  (luma 66.5, ref 140.8)
01:29:23  ## demo start at host time 01:29:23; light clock should read about the same
01:29:23  U demo()
          TX ad060bffffffffffb9  RX -
01:30:14  [cam] camera series 'demo with dim DIY program' (50 samples, changes >= 0.012 shown): 0s=0.472 8s=0.565 14s=0.717 19s=0.472 49.1s=0.475
01:30:15  U status at 50 s
          TX bd0601ffffffffffbf  RX -
01:30:16  re-key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:30:16  U status at 50 s
          TX bd0601ffffffffffbf  RX bd0a81015a0002800064000089  [status on bright=90 speed=0 program program=3 RGBW=0,100,0,0]
01:30:16  U color(0,100,0) back to manual (also stops any demo)
          TX ad0605006400ffff1a  RX -
01:30:16  U status manual
          TX bd0601ffffffffffbf  RX bd0a81015a0000800064000087  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### W wrap-around + fade using a manipulated light clock (DIY 1)

```
01:31:38  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:31:38  W upload frame 0
          TX ad06098003007fffbd  RX -
01:31:38  W upload frame 1
          TX ad080a0000010064000024  RX -
01:31:38  W upload frame 2
          TX ad080a01000300000000c3  RX -
01:31:38  W upload frame 3
          TX ad080a02173b0000000013  RX -
01:31:38  W readback
          TX bd060280ffffffff41  RX bd06828003007fff46  [program header DIY1 count=3]
01:31:39  W readback +listen: first RX bd06828003007fff46  then 3 more: bd088300000100640000ad@9ms bd0883010003000000004c@77ms bd088302173b000000009c@82ms
01:31:40  W program(3)
          TX ad060880ffffffff37  RX -
01:31:40  W status running DIY 1
          TX bd0601ffffffffffbf  RX bd0a81015a0002800064000089  [status on bright=90 speed=0 program program=3 RGBW=0,100,0,0]
01:31:44  [cam] W baseline (real clock ~01:4x: dark expected): ratio 0.472  (luma 66.4, ref 140.7)
01:31:44  ## time_sync to fake clock 23:58:20 (dow 3) at host 01:31:44; events: L 23:59:00=+40s, F 00:01:00=+160s, G 00:03:00=+280s
01:31:45  W time_sync 23:58:20
          TX ad0606173a1403ff20  RX -
01:37:16  [cam] camera series 'wrap+fade (t=0 is time_sync send; L +40 s, F +160 s, G +280 s)' (220 samples, changes >= 0.012 shown): 0s=0.473 43.5s=0.509 46.5s=0.545 51s=0.583 54s=0.601 57s=0.617 60s=0.629 61.5s=0.652 67.6s=0.674 70.6s=0.694 75.1s=0.712 81.1s=0.729 85.6s=0.757 91.6s=0.771 94.6s=0.784 99.1s=0.800 105.1s=0.815 109.6s=0.834 115.6s=0.848 121.6s=0.861 123.1s=0.875 132.1s=0.893 136.6s=0.909 142.6s=0.925 147.1s=0.941 157.6s=0.961 169.6s=0.949 177.1s=0.931 184.6s=0.913 190.6s=0.898 195.2s=0.882 201.2s=0.868 205.7s=0.851 211.7s=0.835 214.7s=0.823 219.2s=0.804 225.2s=0.789 229.7s=0.765 235.7s=0.749 241.7s=0.731 243.2s=0.716 249.2s=0.696 255.2s=0.669 259.7s=0.642 262.7s=0.629 265.7s=0.610 267.2s=0.590 270.2s=0.574 273.2s=0.542 276.2s=0.530 279.2s=0.490 280.7s=0.474 328.8s=0.475
```

### X restore real clock

```
01:37:45  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:37:45  ## time_sync back to real local time 01:37:45 dow 3
01:37:45  X time_sync(01:37:45)
          TX ad060601252d03ff0e  RX -
01:37:46  X status after time sync
          TX bd0601ffffffffffbf  RX bd0a81015a0002800064000089  [status on bright=90 speed=0 program program=3 RGBW=0,100,0,0]
```

### Y demo abort + program brightness (camera)

```
01:37:55  verify_key
          TX bd060a78563412ffe0  RX -
01:37:55  ## demo abort: program 0 (dark at night) -> demo -> after 10 s a colour command; the light must then hold manual green
01:37:55  Y program(0)
          TX ad060800ffffffffb7  RX -
01:38:01  [cam] Y program 0 baseline (dark): ratio 0.475  (luma 66.9, ref 140.8)
01:38:01  Y demo()
          TX ad060bffffffffffb9  RX -
01:38:12  [cam] Y demo running (bright expected): ratio 1.424  (luma 200.4, ref 140.8)
01:38:12  verify_key
          TX bd060a78563412ffe0  RX -
01:38:13  Y color(0,100,0) aborts the demo
          TX ad0605006400ffff1a  RX -
01:38:23  [cam] camera series 'after colour command (manual green 0.96 expected, steady)' (10 samples, changes >= 0.012 shown): 0s=1.417 2s=0.968 9s=0.964
01:38:23  ## brightness while a program runs (DIY 2 = constant blue, stored earlier)
01:38:23  verify_key
          TX bd060a78563412ffe0  RX -
01:38:24  Y program(4) DIY 2
          TX ad060881ffffffff38  RX -
01:38:24  Y brightness(100)
          TX ad060264ffffffff15  RX -
01:38:24  Y status brightness 100 in program
          TX bd0601ffffffffffbf  RX bd0a8101640002810064000094  [status on bright=100 speed=0 program program=4 RGBW=0,100,0,0]
01:38:30  [cam] Y DIY 2 blue at brightness 100: ratio 0.713  (luma 99.4, ref 139.4)
01:38:30  Y brightness(50)
          TX ad060232ffffffffe3  RX -
01:38:31  Y status brightness 50 in program
          TX bd0601ffffffffffbf  RX bd0a8101320002810064000062  [status on bright=50 speed=0 program program=4 RGBW=0,100,0,0]
01:38:36  [cam] Y DIY 2 blue at brightness 50: ratio 0.609  (luma 85.0, ref 139.5)
01:38:36  Y brightness(10)
          TX ad06020affffffffbb  RX -
01:38:36  Y status brightness 10 in program
          TX bd0601ffffffffffbf  RX bd0a81010a000281006400003a  [status on bright=10 speed=0 program program=4 RGBW=0,100,0,0]
01:38:41  [cam] Y DIY 2 blue at brightness 10: ratio 0.512  (luma 71.0, ref 138.8)
01:38:41  Y brightness(90) restore
          TX ad06025affffffff0b  RX -
01:38:41  Y color(0,100,0) back to manual
          TX ad0605006400ffff1a  RX -
01:38:42  Y status
          TX bd0601ffffffffffbf  RX bd0a81015a0000810064000088  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### Z probe program ids beyond DIY 3 (queries only)

```
01:39:03  verify_key
          TX bd060a78563412ffe0  RX -
01:39:03  ## query-only probe of other program ids with the app's own DIY query opcode BD 06 02
01:39:04  Z query id 3
          TX bd060203ffffffffc4  RX -
01:39:04  Z query id 4
          TX bd060204ffffffffc5  RX -
01:39:05  Z query id 5
          TX bd060205ffffffffc6  RX -
01:39:05  Z query id 6
          TX bd060206ffffffffc7  RX -
01:39:06  Z query id 7
          TX bd060207ffffffffc8  RX -
01:39:06  Z query id 127
          TX bd06027fffffffff40  RX -
01:39:07  Z query id 131
          TX bd060283ffffffff44  RX -
01:39:07  Z query id 132
          TX bd060284ffffffff45  RX bd06828003007f81c8  [program header DIY1 count=3]
01:39:07  Z query id 133
          TX bd060285ffffffff46  RX bd088300000100640000ad  [point #0 00:01 RGBW=0,100,0,0]
01:39:07  Z query id 135
          TX bd060287ffffffff48  RX bd0883010003000000004c  [point #1 00:03 RGBW=0,0,0,0]
01:39:07  Z query id 160
          TX bd0602a0ffffffff61  RX bd088302173b000000009c  [point #2 23:59 RGBW=0,0,0,0]
01:39:07  Z query id 255
          TX bd0602ffffffffffc0  RX bd06828103007f0048  [program header DIY2 count=3]
01:39:07  ## answered ids: {132: 'bd06828003007f81c8', 133: 'bd088300000100640000ad', 135: 'bd0883010003000000004c', 160: 'bd088302173b000000009c', 255: 'bd06828103007f0048'}
01:39:07  Z status
          TX bd0601ffffffffffbf  RX bd088300000100640000ad  [point #0 00:01 RGBW=0,100,0,0]
```

### Z2 program id probe with drain (queries only)

```
01:39:36  verify_key
          TX bd060a78563412ffe0  RX -
01:39:37  Z2 query id 128
          TX bd060280ffffffff41  RX bd06828003007f81c8  [program header DIY1 count=3]
01:39:38  ## id 128 (0x80): first=bd06828003007f81c8 then 3 more: ['bd088300000100640000ad', 'bd0883010003000000004c', 'bd088302173b000000009c']
01:39:38  Z2 query id 129
          TX bd060281ffffffff42  RX bd06828103007f0048  [program header DIY2 count=3]
01:39:39  ## id 129 (0x81): first=bd06828103007f0048 then 3 more: ['bd088300000000006400ac', 'bd088301080000006400b5', 'bd088302100000006400be']
01:39:40  Z2 query id 130
          TX bd060282ffffffff43  RX bd06828200007f0046  [program header DIY3 count=0]
01:39:41  ## id 130 (0x82): first=bd06828200007f0046 then 0 more: []
01:39:42  Z2 query id 131
          TX bd060283ffffffff44  RX -
01:39:43  ## id 131 (0x83): first=None then 0 more: []
01:39:44  Z2 query id 132
          TX bd060284ffffffff45  RX bd06828003007f0047  [program header DIY1 count=3]
01:39:45  ## id 132 (0x84): first=bd06828003007f0047 then 3 more: ['bd088300000100640000ad', 'bd0883010003000000004c', 'bd088302173b000000009c']
01:39:45  Z2 query id 133
          TX bd060285ffffffff46  RX bd06828103007f0048  [program header DIY2 count=3]
01:39:46  ## id 133 (0x85): first=bd06828103007f0048 then 3 more: ['bd088300000000006400ac', 'bd088301080000006400b5', 'bd088302100000006400be']
01:39:47  Z2 query id 134
          TX bd060286ffffffff47  RX bd06828200007f0046  [program header DIY3 count=0]
01:39:48  ## id 134 (0x86): first=bd06828200007f0046 then 0 more: []
01:39:49  Z2 query id 135
          TX bd060287ffffffff48  RX -
01:39:50  ## id 135 (0x87): first=None then 0 more: []
01:39:50  Z2 query id 136
          TX bd060288ffffffff49  RX bd06828003007f0047  [program header DIY1 count=3]
01:39:51  ## id 136 (0x88): first=bd06828003007f0047 then 3 more: ['bd088300000100640000ad', 'bd0883010003000000004c', 'bd088302173b000000009c']
01:39:52  Z2 query id 140
          TX bd06028cffffffff4d  RX bd06828003007f0047  [program header DIY1 count=3]
01:39:53  ## id 140 (0x8C): first=bd06828003007f0047 then 3 more: ['bd088300000100640000ad', 'bd0883010003000000004c', 'bd088302173b000000009c']
01:39:53  Z2 query id 160
          TX bd0602a0ffffffff61  RX bd06828003007f0047  [program header DIY1 count=3]
01:39:54  ## id 160 (0xA0): first=bd06828003007f0047 then 3 more: ['bd088300000100640000ad', 'bd0883010003000000004c', 'bd088302173b000000009c']
01:39:55  Z2 query id 192
          TX bd0602c0ffffffff81  RX bd06828003007f0047  [program header DIY1 count=3]
01:39:56  ## id 192 (0xC0): first=bd06828003007f0047 then 3 more: ['bd088300000100640000ad', 'bd0883010003000000004c', 'bd088302173b000000009c']
01:39:57  Z2 query id 255
          TX bd0602ffffffffffc0  RX -
01:39:58  ## id 255 (0xFF): first=None then 0 more: []
01:39:59  Z2 query id 3
          TX bd060203ffffffffc4  RX -
01:40:00  ## id   3 (0x03): first=None then 0 more: []
01:40:01  Z2 query id 127
          TX bd06027fffffffff40  RX -
01:40:02  ## id 127 (0x7F): first=None then 0 more: []
```

### AA clear DIY 1-3 (restore original: all empty)

```
01:40:13  verify_key
          TX bd060a78563412ffe0  RX -
01:40:14  AA clear DIY 1 frame 0
          TX ad06098000007fffba  RX -
01:40:14  AA clear DIY 2 frame 0
          TX ad06098100007fffbb  RX -
01:40:15  AA clear DIY 3 frame 0
          TX ad06098200007fffbc  RX -
01:40:15  AA readback DIY 1
          TX bd060280ffffffff41  RX bd06828000007f0044  [program header DIY1 count=0]
01:40:16  AA readback DIY 1 +listen: first RX bd06828000007f0044  then 0 more: 
01:40:16  ## clear check DIY 1: ProgramHeader(program=3, count=0) extra=0
01:40:16  AA readback DIY 2
          TX bd060281ffffffff42  RX bd06828100007f0045  [program header DIY2 count=0]
01:40:17  AA readback DIY 2 +listen: first RX bd06828100007f0045  then 0 more: 
01:40:17  ## clear check DIY 2: ProgramHeader(program=4, count=0) extra=0
01:40:17  AA readback DIY 3
          TX bd060282ffffffff43  RX bd06828200007f0046  [program header DIY3 count=0]
01:40:18  AA readback DIY 3 +listen: first RX bd06828200007f0046  then 0 more: 
01:40:18  ## clear check DIY 3: ProgramHeader(program=5, count=0) extra=0
01:40:18  AA status
          TX bd0601ffffffffffbf  RX bd0a81015a0000810064000088  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### FINAL conformance run (protocol.py only)

```
01:46:34  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:46:34  FINAL baseline status
          TX bd0601ffffffffffbf  RX bd0a81015a0000810064000088  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:46:34  ## baseline Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=100, blue=0, white=0)
01:46:34  power(False)
          TX ad060100ffffffffb0  RX -
01:46:34  after power(False)
          TX bd0601ffffffffffbf  RX bd0a81005a0000810064000087  [status off bright=90 speed=0 manual RGBW=0,100,0,0]
01:46:35  power(True)
          TX ad060101ffffffffb1  RX -
01:46:35  after power(True)
          TX bd0601ffffffffffbf  RX bd0a81015a0000810064000088  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:46:35  brightness(60)
          TX ad06023cffffffffed  RX -
01:46:35  after brightness(60)
          TX bd0601ffffffffffbf  RX bd0a81013c000081006400006a  [status on bright=60 speed=0 manual RGBW=0,100,0,0]
01:46:36  brightness(90)
          TX ad06025affffffff0b  RX -
01:46:36  after brightness(90)
          TX bd0601ffffffffffbf  RX bd0a81015a0000810064000088  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:46:36  color(0,0,0)
          TX ad0605000000ffffb6  RX -
01:46:36  after color(0,0,0)
          TX bd0601ffffffffffbf  RX bd0a81015a0000810000000024  [status on bright=90 speed=0 manual RGBW=0,0,0,0]
01:46:37  channel(0,60)
          TX ad06043c00fffffff0  RX -
01:46:37  after channel(0,60)
          TX bd0601ffffffffffbf  RX bd0a81015a0000813c00000060  [status on bright=90 speed=0 manual RGBW=60,0,0,0]
01:46:37  channel reset
          TX ad06040000ffffffb4  RX -
01:46:37  channel(1,60)
          TX ad06043c01fffffff1  RX -
01:46:38  after channel(1,60)
          TX bd0601ffffffffffbf  RX bd0a81015a000081003c000060  [status on bright=90 speed=0 manual RGBW=0,60,0,0]
01:46:38  channel reset
          TX ad06040001ffffffb5  RX -
01:46:38  channel(2,60)
          TX ad06043c02fffffff2  RX -
01:46:38  after channel(2,60)
          TX bd0601ffffffffffbf  RX bd0a81015a00008100003c0060  [status on bright=90 speed=0 manual RGBW=0,0,60,0]
01:46:38  channel reset
          TX ad06040002ffffffb6  RX -
01:46:39  channel(3,60)
          TX ad06043c03fffffff3  RX -
01:46:39  after channel(3,60)
          TX bd0601ffffffffffbf  RX bd0a81015a0000810000003c60  [status on bright=90 speed=0 manual RGBW=0,0,0,60]
01:46:39  channel reset
          TX ad06040003ffffffb7  RX -
01:46:39  color(37,62,81)
          TX ad0605253e51ffff6a  RX -
01:46:40  after color(37,62,81)
          TX bd0601ffffffffffbf  RX bd0a81015a000081253e5100d8  [status on bright=90 speed=0 manual RGBW=37,62,81,0]
01:46:40  white(30)
          TX ad06041e03ffffffd5  RX -
01:46:40  after white(30)
          TX bd0601ffffffffffbf  RX bd0a81015a000081253e511ef6  [status on bright=90 speed=0 manual RGBW=37,62,81,30]
01:46:40  white(0)
          TX ad06040003ffffffb7  RX -
01:46:41  scenario(2)
          TX ad060702ffffffffb8  RX -
01:46:41  after scenario(2)
          TX bd0601ffffffffffbf  RX bd0a81015a000102253e51005a  [status on bright=90 speed=0 scenario scenario=2 RGBW=37,62,81,0]
01:46:41  program(1)
          TX ad060801ffffffffb8  RX -
01:46:42  after program(1)
          TX bd0601ffffffffffbf  RX bd0a81015a000201253e51005a  [status on bright=90 speed=0 program program=1 RGBW=37,62,81,0]
01:46:42  color -> manual
          TX ad0605006400ffff1a  RX -
01:46:42  after color(0,100,0)
          TX bd0601ffffffffffbf  RX bd0a81015a0000010064000008  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:46:42  preview
          TX ad060c010000640024  RX -
01:46:43  during preview
          TX bd0601ffffffffffbf  RX bd0a81015a0000010064000008  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:46:43  preview end
          TX ad060c0000000000bf  RX -
01:46:43  after preview end
          TX bd0601ffffffffffbf  RX bd0a81015a0000010064000008  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:46:43  DIY 2 upload frame 0
          TX ad06098104007fffbf  RX -
01:46:43  DIY 2 upload frame 1
          TX ad080a0000000000640023  RX -
01:46:43  DIY 2 upload frame 2
          TX ad080a01060f0a141e2839  RX -
01:46:43  DIY 2 upload frame 3
          TX ad080a020c006400006495  RX -
01:46:43  DIY 2 upload frame 4
          TX ad080a03173b010203041e  RX -
01:46:44  read program 4
          TX bd060281ffffffff42  RX bd06828104007f014a  [program header DIY2 count=4]
01:46:45  program(4)
          TX ad060881ffffffff38  RX -
01:46:46  after program(4)
          TX bd0601ffffffffffbf  RX bd0a81015a000281006400008a  [status on bright=90 speed=0 program program=4 RGBW=0,100,0,0]
01:46:46  DIY 3 single point frame 0
          TX ad06098201007fffbd  RX -
01:46:46  DIY 3 single point frame 1
          TX ad080a000c00000064002f  RX -
01:46:46  read program 5
          TX bd060282ffffffff43  RX bd06828201007f81c8  [program header DIY3 count=1]
01:46:48  program(5)
          TX ad060882ffffffff39  RX -
01:46:48  after program(5)
          TX bd0601ffffffffffbf  RX bd0a81015a000282006400008b  [status on bright=90 speed=0 program program=5 RGBW=0,100,0,0]
01:46:55  [cam] single-point program at 01:5x (blue expected ~0.70, dark would be 0.47): ratio 0.695 0.694  (luma 96.8, ref 139.5)
01:46:55  clear DIY 2 frame 0
          TX ad06098100007fffbb  RX -
01:46:55  read program 4
          TX bd060281ffffffff42  RX bd06828100007f82c7  [program header DIY2 count=0]
01:46:56  clear DIY 3 frame 0
          TX ad06098200007fffbc  RX -
01:46:56  read program 5
          TX bd060282ffffffff43  RX bd06828200007f82c8  [program header DIY3 count=0]
01:46:57  read program 3
          TX bd060280ffffffff41  RX bd06828000007f82c6  [program header DIY1 count=0]
01:46:58  time_sync(01:46:58)
          TX ad0606012e3a03ff24  RX -
01:46:59  after time_sync
          TX bd0601ffffffffffbf  RX bd0a81015a000282006400008b  [status on bright=90 speed=0 program program=5 RGBW=0,100,0,0]
01:46:59  scenario(7)
          TX ad060707ffffffffbd  RX -
01:46:59  color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:46:59  white(0)
          TX ad06040003ffffffb7  RX -
01:46:59  brightness(90)
          TX ad06025affffffff0b  RX -
01:46:59  power(True)
          TX ad060101ffffffffb1  RX -
01:46:59  FINAL status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
01:47:08  [cam] final look (manual green 90 -> ~0.96): ratio 0.969 0.967  (luma 136.2, ref 140.8)
```

### FINAL

```
01:46:34  ## PASS after power(False): {'power': False} -> Status(power=False, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=100, blue=0, white=0)
01:46:35  ## PASS after power(True): {'power': True} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=100, blue=0, white=0)
01:46:35  ## PASS after brightness(60): {'brightness': 60} -> Status(power=True, brightness=60, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=100, blue=0, white=0)
01:46:36  ## PASS after brightness(90): {'brightness': 90} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=100, blue=0, white=0)
01:46:36  ## PASS after color(0,0,0): {'mode': <Mode.MANUAL: 0>, 'red': 0, 'green': 0, 'blue': 0, 'white': 0} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=0, blue=0, white=0)
01:46:37  ## PASS after channel(0,60): {'red': 60} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=60, green=0, blue=0, white=0)
01:46:38  ## PASS after channel(1,60): {'green': 60} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=60, blue=0, white=0)
01:46:38  ## PASS after channel(2,60): {'blue': 60} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=0, blue=60, white=0)
01:46:39  ## PASS after channel(3,60): {'white': 60} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=0, blue=0, white=60)
01:46:40  ## PASS after color(37,62,81): {'red': 37, 'green': 62, 'blue': 81} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=37, green=62, blue=81, white=0)
01:46:40  ## PASS after white(30): {'white': 30} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=37, green=62, blue=81, white=30)
01:46:41  ## PASS after scenario(2): {'mode': <Mode.SCENARIO: 1>, 'scenario': 2} -> Status(power=True, brightness=90, speed=0, mode=<Mode.SCENARIO: 1>, scenario=2, program=None, red=37, green=62, blue=81, white=0)
01:46:42  ## PASS after program(1): {'mode': <Mode.PROGRAM: 2>, 'program': 1} -> Status(power=True, brightness=90, speed=0, mode=<Mode.PROGRAM: 2>, scenario=None, program=1, red=37, green=62, blue=81, white=0)
01:46:42  ## PASS after color(0,100,0): {'mode': <Mode.MANUAL: 0>} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=100, blue=0, white=0)
01:46:43  ## PASS during preview: {'mode': <Mode.MANUAL: 0>, 'red': 0, 'green': 100, 'blue': 0} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=100, blue=0, white=0)
01:46:43  ## PASS after preview end: {'mode': <Mode.MANUAL: 0>} -> Status(power=True, brightness=90, speed=0, mode=<Mode.MANUAL: 0>, scenario=None, program=None, red=0, green=100, blue=0, white=0)
01:46:45  ## PASS DIY 2 read back byte-exact: (4, [Point(hour=0, minute=0, red=0, green=0, blue=100, white=0), Point(hour=6, minute=15, red=10, green=20, blue=30, white=40), Point(hour=12, minute=0, red=100, green=0, blue=0, white=100), Point(hour=23, minute=59, red=1, green=2, blue=3, white=4)])
01:46:46  ## PASS after program(4): {'mode': <Mode.PROGRAM: 2>, 'program': 4} -> Status(power=True, brightness=90, speed=0, mode=<Mode.PROGRAM: 2>, scenario=None, program=4, red=0, green=100, blue=0, white=0)
01:46:47  ## PASS DIY 3 single point read back: (5, [Point(hour=12, minute=0, red=0, green=0, blue=100, white=0)])
01:46:48  ## PASS after program(5): {'mode': <Mode.PROGRAM: 2>, 'program': 5} -> Status(power=True, brightness=90, speed=0, mode=<Mode.PROGRAM: 2>, scenario=None, program=5, red=0, green=100, blue=0, white=0)
01:46:55  ## PASS single point holds its colour before its time: ratios [0.695, 0.694]
01:46:56  ## PASS DIY 2 empty again: (4, [])
01:46:57  ## PASS DIY 3 empty again: (5, [])
01:46:58  ## PASS DIY 1 empty: (3, [])
01:46:59  ## PASS after time_sync: {'power': True} -> Status(power=True, brightness=90, speed=0, mode=<Mode.PROGRAM: 2>, scenario=None, program=5, red=0, green=100, blue=0, white=0)
01:46:59  ## PASS FINAL status equals the ORIGINAL status bd0a81015a000007006400000e: bd0a81015a000007006400000e
01:47:08  ## PASS final light output is manual green: [0.969, 0.967]
02:03:44  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
02:03:44  closing time_sync(02:03:44) so the light clock matches local time
          TX ad060602032c03ffec  RX -
02:03:44  FINAL status (closing read)
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
02:03:44  ## PASS closing status bd0a81015a000007006400000e equals the ORIGINAL status bd0a81015a000007006400000e
02:03:44  closing read DIY 1
          TX bd060280ffffffff41  RX bd06828000007f074b  [program header DIY1 count=0]
02:03:44  closing read DIY 2
          TX bd060281ffffffff42  RX bd06828100007f074c  [program header DIY2 count=0]
02:03:44  closing read DIY 3
          TX bd060282ffffffff43  RX bd06828200007f074d  [program header DIY3 count=0]
02:03:47  [cam] closing look (manual green): ratio 1.358  (luma 146.6, ref 108.0)
```

### PC preset curves vs firmware (manipulated clock + camera)

```
01:50:23  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:50:24  PC time_sync 07:07:00
          TX ad060607070003ffc9  RX -
01:50:24  PC program(0)
          TX ad060800ffffffffb7  RX -
01:50:31  [cam] PC preset 0 at 07:07 (program): ratio 1.145 1.151  (luma 163.9, ref 142.5)
01:50:32  PC manual colour (33, 33, 33, 33)
          TX ad0605212121ffff19  RX -
01:50:32  PC manual white 33
          TX ad06042103ffffffd8  RX -
01:50:39  [cam] PC manual (33, 33, 33, 33) (same colour set by hand): ratio 1.151 1.150  (luma 164.1, ref 142.7)
01:50:39  ## preset 0 at 07:07: interpolated RGBW (33, 33, 33, 33): program ratio 1.148 vs same colour by hand 1.151 (diff 0.002)
01:50:40  PC time_sync 18:07:00
          TX ad060612070003ffd4  RX -
01:50:40  PC program(0)
          TX ad060800ffffffffb7  RX -
01:50:47  [cam] PC preset 0 at 18:07 (program): ratio 1.202 1.198  (luma 168.1, ref 140.4)
01:50:48  PC manual colour (37, 37, 84, 37)
          TX ad0605252554ffff54  RX -
01:50:48  PC manual white 37
          TX ad06042503ffffffdc  RX -
01:50:55  [cam] PC manual (37, 37, 84, 37) (same colour set by hand): ratio 1.203 1.204  (luma 168.9, ref 140.2)
01:50:55  ## preset 0 at 18:07: interpolated RGBW (37, 37, 84, 37): program ratio 1.200 vs same colour by hand 1.204 (diff 0.004)
01:50:56  PC time_sync 10:00:00
          TX ad06060a000003ffc5  RX -
01:50:56  PC program(1)
          TX ad060801ffffffffb8  RX -
01:51:03  [cam] PC preset 1 at 10:00 (program): ratio 1.237 1.236  (luma 174.4, ref 141.1)
01:51:04  PC manual colour (82, 15, 10, 75)
          TX ad0605520f0affff21  RX -
01:51:04  PC manual white 75
          TX ad06044b03ffffff02  RX -
01:51:11  [cam] PC manual (82, 15, 10, 75) (same colour set by hand): ratio 1.309 1.309  (luma 182.5, ref 139.4)
01:51:11  ## preset 1 at 10:00: interpolated RGBW (82, 15, 10, 75): program ratio 1.236 vs same colour by hand 1.309 (diff 0.073)
01:51:12  PC time_sync 18:30:00
          TX ad0606121e0003ffeb  RX -
01:51:12  PC program(1)
          TX ad060801ffffffffb8  RX -
01:51:19  [cam] PC preset 1 at 18:30 (program): ratio 0.937 0.928  (luma 129.7, ref 139.8)
01:51:20  PC manual colour (25, 30, 55, 5)
          TX ad0605191e37ffff24  RX -
01:51:20  PC manual white 5
          TX ad06040503ffffffbc  RX -
01:51:27  [cam] PC manual (25, 30, 55, 5) (same colour set by hand): ratio 0.937 0.939  (luma 131.3, ref 139.9)
01:51:27  ## preset 1 at 18:30: interpolated RGBW (25, 30, 55, 5): program ratio 0.933 vs same colour by hand 0.938 (diff 0.005)
01:51:28  PC time_sync 08:15:00
          TX ad0606080f0003ffd2  RX -
01:51:28  PC program(2)
          TX ad060802ffffffffb9  RX -
01:51:35  [cam] PC preset 2 at 08:15 (program): ratio 0.911 0.910  (luma 127.2, ref 139.8)
01:51:36  PC manual colour (50, 25, 5, 5)
          TX ad0605321905ffff06  RX -
01:51:36  PC manual white 5
          TX ad06040503ffffffbc  RX -
01:51:44  [cam] PC manual (50, 25, 5, 5) (same colour set by hand): ratio 0.907 0.907  (luma 126.8, ref 139.8)
01:51:44  ## preset 2 at 08:15: interpolated RGBW (50, 25, 5, 5): program ratio 0.911 vs same colour by hand 0.907 (diff 0.004)
01:51:44  PC time_sync 18:30:00
          TX ad0606121e0003ffeb  RX -
01:51:44  PC program(2)
          TX ad060802ffffffffb9  RX -
01:51:52  [cam] PC preset 2 at 18:30 (program): ratio 0.995 0.991  (luma 139.1, ref 140.4)
01:51:52  PC manual colour (50, 50, 58, 0)
          TX ad060532323affff54  RX -
01:51:52  PC manual white 0
          TX ad06040003ffffffb7  RX -
01:52:00  [cam] PC manual (50, 50, 58, 0) (same colour set by hand): ratio 0.995 0.990  (luma 139.3, ref 140.7)
01:52:00  ## preset 2 at 18:30: interpolated RGBW (50, 50, 58, 0): program ratio 0.993 vs same colour by hand 0.992 (diff 0.001)
01:52:00  PC time_sync real time
          TX ad060601340003fff0  RX -
01:52:00  PC scenario(7)
          TX ad060707ffffffffbd  RX -
01:52:00  PC color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:52:00  PC white(0)
          TX ad06040003ffffffb7  RX -
01:52:00  PC final status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### PC2 preset curves vs firmware (manipulated clock + camera), detailed

```
01:53:10  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:53:10  PC time_sync 06:00:00
          TX ad060606000003ffc1  RX -
01:53:10  PC program(1)
          TX ad060801ffffffffb8  RX -
01:53:14  [cam] PC preset 1 at 06:00 (program): ratio 0.470  (luma 66.4, ref 141.4)
01:53:14  PC manual colour (0, 0, 0, 0)
          TX ad0605000000ffffb6  RX -
01:53:15  PC manual white 0
          TX ad06040003ffffffb7  RX -
01:53:20  [cam] PC manual (0, 0, 0, 0) (same colour set by hand): ratio 0.475  (luma 67.1, ref 141.2)
01:53:20  ## preset 1 at 06:00: interpolated RGBW (0, 0, 0, 0): program ratio 0.470 vs same colour by hand 0.475 (diff 0.005)
01:53:20  PC time_sync 07:00:00
          TX ad060607000003ffc2  RX -
01:53:20  PC program(1)
          TX ad060801ffffffffb8  RX -
01:53:25  [cam] PC preset 1 at 07:00 (program): ratio 0.806  (luma 114.7, ref 142.2)
01:53:25  PC manual colour (25, 6, 2, 8)
          TX ad0605190602ffffd7  RX -
01:53:25  PC manual white 8
          TX ad06040803ffffffbf  RX -
01:53:30  [cam] PC manual (25, 6, 2, 8) (same colour set by hand): ratio 0.823  (luma 117.2, ref 142.3)
01:53:30  ## preset 1 at 07:00: interpolated RGBW (25, 6, 2, 8): program ratio 0.806 vs same colour by hand 0.823 (diff 0.017)
01:53:31  PC time_sync 08:00:00
          TX ad060608000003ffc3  RX -
01:53:31  PC program(1)
          TX ad060801ffffffffb8  RX -
01:53:36  [cam] PC preset 1 at 08:00 (program): ratio 0.983  (luma 141.2, ref 143.7)
01:53:36  PC manual colour (50, 12, 3, 15)
          TX ad0605320c03fffff7  RX -
01:53:36  PC manual white 15
          TX ad06040f03ffffffc6  RX -
01:53:41  [cam] PC manual (50, 12, 3, 15) (same colour set by hand): ratio 0.986  (luma 141.2, ref 143.2)
01:53:41  ## preset 1 at 08:00: interpolated RGBW (50, 12, 3, 15): program ratio 0.983 vs same colour by hand 0.986 (diff 0.003)
01:53:41  PC time_sync 09:00:00
          TX ad060609000003ffc4  RX -
01:53:41  PC program(1)
          TX ad060801ffffffffb8  RX -
01:53:46  [cam] PC preset 1 at 09:00 (program): ratio 1.142  (luma 160.6, ref 140.6)
01:53:47  PC manual colour (65, 30, 20, 50)
          TX ad0605411e14ffff29  RX -
01:53:47  PC manual white 50
          TX ad06043203ffffffe9  RX -
01:53:52  [cam] PC manual (65, 30, 20, 50) (same colour set by hand): ratio 1.233  (luma 175.6, ref 142.5)
01:53:52  ## preset 1 at 09:00: interpolated RGBW (65, 30, 20, 50): program ratio 1.142 vs same colour by hand 1.233 (diff 0.091)
01:53:52  PC time_sync 10:00:00
          TX ad06060a000003ffc5  RX -
01:53:52  PC program(1)
          TX ad060801ffffffffb8  RX -
01:53:57  [cam] PC preset 1 at 10:00 (program): ratio 1.231  (luma 174.8, ref 142.0)
01:53:57  PC manual colour (82, 15, 10, 75)
          TX ad0605520f0affff21  RX -
01:53:57  PC manual white 75
          TX ad06044b03ffffff02  RX -
01:54:02  [cam] PC manual (82, 15, 10, 75) (same colour set by hand): ratio 1.310  (luma 182.6, ref 139.5)
01:54:02  ## preset 1 at 10:00: interpolated RGBW (82, 15, 10, 75): program ratio 1.231 vs same colour by hand 1.310 (diff 0.079)
01:54:03  PC time_sync 11:00:00
          TX ad06060b000003ffc6  RX -
01:54:03  PC program(1)
          TX ad060801ffffffffb8  RX -
01:54:08  [cam] PC preset 1 at 11:00 (program): ratio 1.356  (luma 190.9, ref 140.8)
01:54:08  PC manual colour (100, 0, 0, 100)
          TX ad0605640000ffff1a  RX -
01:54:08  PC manual white 100
          TX ad06046403ffffff1b  RX -
01:54:13  [cam] PC manual (100, 0, 0, 100) (same colour set by hand): ratio 1.352  (luma 190.7, ref 141.0)
01:54:13  ## preset 1 at 11:00: interpolated RGBW (100, 0, 0, 100): program ratio 1.356 vs same colour by hand 1.352 (diff 0.004)
01:54:13  PC time_sync 12:00:00
          TX ad06060c000003ffc7  RX -
01:54:13  PC program(1)
          TX ad060801ffffffffb8  RX -
01:54:18  [cam] PC preset 1 at 12:00 (program): ratio 1.380  (luma 195.5, ref 141.7)
01:54:19  PC manual colour (100, 50, 50, 100)
          TX ad0605643232ffff7e  RX -
01:54:19  PC manual white 100
          TX ad06046403ffffff1b  RX -
01:54:24  [cam] PC manual (100, 50, 50, 100) (same colour set by hand): ratio 1.378  (luma 195.0, ref 141.6)
01:54:24  ## preset 1 at 12:00: interpolated RGBW (100, 50, 50, 100): program ratio 1.380 vs same colour by hand 1.378 (diff 0.002)
01:54:24  PC time_sync 13:00:00
          TX ad06060d000003ffc8  RX -
01:54:24  PC program(1)
          TX ad060801ffffffffb8  RX -
01:54:29  [cam] PC preset 1 at 13:00 (program): ratio 1.419  (luma 196.9, ref 138.7)
01:54:29  PC manual colour (100, 100, 100, 100)
          TX ad0605646464ffffe2  RX -
01:54:29  PC manual white 100
          TX ad06046403ffffff1b  RX -
01:54:34  [cam] PC manual (100, 100, 100, 100) (same colour set by hand): ratio 1.419  (luma 196.5, ref 138.5)
01:54:34  ## preset 1 at 13:00: interpolated RGBW (100, 100, 100, 100): program ratio 1.419 vs same colour by hand 1.419 (diff 0.000)
01:54:35  PC time_sync 14:30:00
          TX ad06060e1e0003ffe7  RX -
01:54:35  PC program(1)
          TX ad060801ffffffffb8  RX -
01:54:40  [cam] PC preset 1 at 14:30 (program): ratio 1.415  (luma 195.3, ref 138.1)
01:54:40  PC manual colour (85, 85, 100, 100)
          TX ad0605555564ffffc4  RX -
01:54:40  PC manual white 100
          TX ad06046403ffffff1b  RX -
01:54:45  [cam] PC manual (85, 85, 100, 100) (same colour set by hand): ratio 1.412  (luma 195.0, ref 138.1)
01:54:45  ## preset 1 at 14:30: interpolated RGBW (85, 85, 100, 100): program ratio 1.415 vs same colour by hand 1.412 (diff 0.003)
01:54:45  PC time_sync 16:00:00
          TX ad060610000003ffcb  RX -
01:54:45  PC program(1)
          TX ad060801ffffffffb8  RX -
01:54:50  [cam] PC preset 1 at 16:00 (program): ratio 1.402  (luma 193.3, ref 137.8)
01:54:51  PC manual colour (70, 70, 100, 100)
          TX ad0605464664ffffa6  RX -
01:54:51  PC manual white 100
          TX ad06046403ffffff1b  RX -
01:54:56  [cam] PC manual (70, 70, 100, 100) (same colour set by hand): ratio 1.404  (luma 193.4, ref 137.7)
01:54:56  ## preset 1 at 16:00: interpolated RGBW (70, 70, 100, 100): program ratio 1.402 vs same colour by hand 1.404 (diff 0.002)
01:54:56  PC time_sync 16:30:00
          TX ad0606101e0003ffe9  RX -
01:54:56  PC program(1)
          TX ad060801ffffffffb8  RX -
01:55:01  [cam] PC preset 1 at 16:30 (program): ratio 1.355  (luma 184.0, ref 135.9)
01:55:01  PC manual colour (60, 60, 75, 75)
          TX ad06053c3c4bffff79  RX -
01:55:01  PC manual white 75
          TX ad06044b03ffffff02  RX -
01:55:06  [cam] PC manual (60, 60, 75, 75) (same colour set by hand): ratio 1.357  (luma 184.7, ref 136.1)
01:55:06  ## preset 1 at 16:30: interpolated RGBW (60, 60, 75, 75): program ratio 1.355 vs same colour by hand 1.357 (diff 0.002)
01:55:07  PC time_sync 17:00:00
          TX ad060611000003ffcc  RX -
01:55:07  PC program(1)
          TX ad060801ffffffffb8  RX -
01:55:12  [cam] PC preset 1 at 17:00 (program): ratio 1.264  (luma 176.4, ref 139.7)
01:55:12  PC manual colour (50, 50, 50, 50)
          TX ad0605323232ffff4c  RX -
01:55:12  PC manual white 50
          TX ad06043203ffffffe9  RX -
01:55:17  [cam] PC manual (50, 50, 50, 50) (same colour set by hand): ratio 1.265  (luma 176.1, ref 139.2)
01:55:17  ## preset 1 at 17:00: interpolated RGBW (50, 50, 50, 50): program ratio 1.264 vs same colour by hand 1.265 (diff 0.001)
01:55:17  PC time_sync 17:30:00
          TX ad0606111e0003ffea  RX -
01:55:17  PC program(1)
          TX ad060801ffffffffb8  RX -
01:55:23  [cam] PC preset 1 at 17:30 (program): ratio 1.150  (luma 158.1, ref 137.5)
01:55:23  PC manual colour (50, 30, 30, 30)
          TX ad0605321e1effff24  RX -
01:55:23  PC manual white 30
          TX ad06041e03ffffffd5  RX -
01:55:28  [cam] PC manual (50, 30, 30, 30) (same colour set by hand): ratio 1.156  (luma 159.1, ref 137.6)
01:55:28  ## preset 1 at 17:30: interpolated RGBW (50, 30, 30, 30): program ratio 1.150 vs same colour by hand 1.156 (diff 0.006)
01:55:28  PC time_sync 18:00:00
          TX ad060612000003ffcd  RX -
01:55:28  PC program(1)
          TX ad060801ffffffffb8  RX -
01:55:33  [cam] PC preset 1 at 18:00 (program): ratio 0.921  (luma 129.7, ref 140.8)
01:55:33  PC manual colour (50, 10, 10, 10)
          TX ad0605320a0afffffc  RX -
01:55:34  PC manual white 10
          TX ad06040a03ffffffc1  RX -
01:55:39  [cam] PC manual (50, 10, 10, 10) (same colour set by hand): ratio 0.926  (luma 130.5, ref 140.9)
01:55:39  ## preset 1 at 18:00: interpolated RGBW (50, 10, 10, 10): program ratio 0.921 vs same colour by hand 0.926 (diff 0.005)
01:55:39  PC time_sync 19:00:00
          TX ad060613000003ffce  RX -
01:55:39  PC program(1)
          TX ad060801ffffffffb8  RX -
01:55:44  [cam] PC preset 1 at 19:00 (program): ratio 0.929  (luma 131.0, ref 141.1)
01:55:44  PC manual colour (0, 50, 100, 0)
          TX ad0605003264ffff4c  RX -
01:55:44  PC manual white 0
          TX ad06040003ffffffb7  RX -
01:55:49  [cam] PC manual (0, 50, 100, 0) (same colour set by hand): ratio 0.931  (luma 131.2, ref 140.8)
01:55:49  ## preset 1 at 19:00: interpolated RGBW (0, 50, 100, 0): program ratio 0.929 vs same colour by hand 0.931 (diff 0.002)
01:55:49  PC time_sync 20:30:00
          TX ad0606141e0003ffed  RX -
01:55:50  PC program(1)
          TX ad060801ffffffffb8  RX -
01:55:55  [cam] PC preset 1 at 20:30 (program): ratio 0.769  (luma 107.6, ref 139.9)
01:55:55  PC manual colour (0, 25, 50, 0)
          TX ad0605001932ffff01  RX -
01:55:55  PC manual white 0
          TX ad06040003ffffffb7  RX -
01:56:00  [cam] PC manual (0, 25, 50, 0) (same colour set by hand): ratio 0.768  (luma 107.5, ref 140.0)
01:56:00  ## preset 1 at 20:30: interpolated RGBW (0, 25, 50, 0): program ratio 0.769 vs same colour by hand 0.768 (diff 0.001)
01:56:00  PC time_sync real time
          TX ad060601380003fff4  RX -
01:56:00  PC scenario(7)
          TX ad060707ffffffffbd  RX -
01:56:00  PC color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:56:01  PC white(0)
          TX ad06040003ffffffb7  RX -
01:56:01  PC final status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### PC3 Dutch 09:00 candidates

```
01:56:19  verify_key
          TX bd060a78563412ffe0  RX -
01:56:19  PC3 colour (67, 8, 2)
          TX ad0605430802ffff03  RX -
01:56:19  PC3 white 43
          TX ad06042b03ffffffe2  RX -
01:56:25  [cam] PC3 manual (67, 8, 2, 43): ratio 1.515 1.509  (luma 164.6, ref 109.1)
01:56:25  ## cand (67, 8, 2, 43): ratio 1.512  (linear 08:00->11:00 at 09:00 (program measured 1.142))
01:56:25  PC3 colour (83, 4, 1)
          TX ad0605530401ffff0e  RX -
01:56:25  PC3 white 72
          TX ad06044803ffffffff  RX -
01:56:33  [cam] PC3 manual (83, 4, 1, 72): ratio 1.624 1.613  (luma 179.9, ref 111.5)
01:56:33  ## cand (83, 4, 1, 72): ratio 1.619  (linear 08:00->11:00 at 10:00 (program measured 1.231-1.236))
01:56:33  PC3 colour (65, 30, 20)
          TX ad0605411e14ffff29  RX -
01:56:33  PC3 white 25
          TX ad06041903ffffffd0  RX -
01:56:41  [cam] PC3 manual (65, 30, 20, 25): ratio 1.441 1.439  (luma 157.4, ref 109.3)
01:56:41  ## cand (65, 30, 20, 25): ratio 1.440  (app rgb with half white)
01:56:41  PC3 colour (65, 30, 20)
          TX ad0605411e14ffff29  RX -
01:56:41  PC3 white 50
          TX ad06043203ffffffe9  RX -
01:56:49  [cam] PC3 manual (65, 30, 20, 50): ratio 1.596 1.586  (luma 178.1, ref 112.3)
01:56:49  ## cand (65, 30, 20, 50): ratio 1.591  (app table 09:00 (1.233 measured before))
01:56:49  PC3 colour (58, 21, 12)
          TX ad06053a150cffff11  RX -
01:56:49  PC3 white 32
          TX ad06042003ffffffd7  RX -
01:56:57  [cam] PC3 manual (58, 21, 12, 32): ratio 1.437 1.442  (luma 157.5, ref 109.2)
01:56:57  ## cand (58, 21, 12, 32): ratio 1.440  (midway between the 08:00 point and the app 09:00 point)
01:56:57  PC3 scenario(7)
          TX ad060707ffffffffbd  RX -
01:56:57  PC3 color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:56:58  PC3 white(0)
          TX ad06040003ffffffb7  RX -
01:56:58  PC3 final status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### PC4 Dutch curve, luma-based

```
01:57:26  verify_key
          TX bd060a78563412ffe0  RX -
01:57:26  PC4 time_sync 9:00
          TX ad060609000003ffc4  RX -
01:57:26  PC4 program(1)
          TX ad060801ffffffffb8  RX -
01:57:33  [cam] PC4 Dutch at 9:00: ratio 1.472 1.468  (luma 161.9, ref 110.3)
01:57:33  PC4 time_sync 10:00
          TX ad06060a000003ffc5  RX -
01:57:33  PC4 program(1)
          TX ad060801ffffffffb8  RX -
01:57:41  [cam] PC4 Dutch at 10:00: ratio 1.592 1.584  (luma 177.9, ref 112.3)
01:57:41  PC4 colour (65, 30, 20)
          TX ad0605411e14ffff29  RX -
01:57:41  PC4 white 50
          TX ad06043203ffffffe9  RX -
01:57:49  [cam] PC4 manual (65, 30, 20, 50): ratio 1.596 1.584  (luma 178.3, ref 112.6)
01:57:49  PC4 colour (67, 8, 2)
          TX ad0605430802ffff03  RX -
01:57:49  PC4 white 43
          TX ad06042b03ffffffe2  RX -
01:57:57  [cam] PC4 manual (67, 8, 2, 43): ratio 1.547 1.550  (luma 174.6, ref 112.7)
01:57:57  PC4 colour (83, 4, 1)
          TX ad0605530401ffff0e  RX -
01:57:57  PC4 white 72
          TX ad06044803ffffffff  RX -
01:58:05  [cam] PC4 manual (83, 4, 1, 72): ratio 1.597 1.594  (luma 178.3, ref 111.9)
01:58:05  PC4 colour (65, 30, 20)
          TX ad0605411e14ffff29  RX -
01:58:05  PC4 white 25
          TX ad06041903ffffffd0  RX -
01:58:13  [cam] PC4 manual (65, 30, 20, 25): ratio 1.442 1.444  (luma 157.2, ref 108.9)
01:58:13  PC4 colour (65, 15, 8)
          TX ad0605410f08ffff0e  RX -
01:58:13  PC4 white 40
          TX ad06042803ffffffdf  RX -
01:58:21  [cam] PC4 manual (65, 15, 8, 40): ratio 1.460 1.460  (luma 160.0, ref 109.5)
01:58:21  PC4 colour (82, 15, 10)
          TX ad0605520f0affff21  RX -
01:58:21  PC4 white 75
          TX ad06044b03ffffff02  RX -
01:58:29  [cam] PC4 manual (82, 15, 10, 75): ratio 1.613 1.608  (luma 179.8, ref 111.8)
01:58:29  PC4 colour (82, 15, 10)
          TX ad0605520f0affff21  RX -
01:58:29  PC4 white 50
          TX ad06043203ffffffe9  RX -
01:58:37  [cam] PC4 manual (82, 15, 10, 50): ratio 1.586 1.584  (luma 176.8, ref 111.6)
01:58:37  PC4 time_sync real
          TX ad0606013a2503ff1b  RX -
01:58:37  PC4 scenario(7)
          TX ad060707ffffffffbd  RX -
01:58:38  PC4 color(0,100,0)
          TX ad0605006400ffff1a  RX -
01:58:38  PC4 white(0)
          TX ad06040003ffffffb7  RX -
01:58:38  PC4 final status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

### PC5 Dutch 09:00-10:30 hypotheses

```
01:59:33  verify_key
          TX bd060a78563412ffe0  RX bd068a01ffffffff4a  [key ok]
01:59:33  PC5 time_sync 9:00
          TX ad060609000003ffc4  RX -
01:59:33  PC5 program(1)
          TX ad060801ffffffffb8  RX -
01:59:41  [cam] PC5 Dutch program at 9:00: ratio 1.478 1.473  (luma 162.4, ref 110.2)
01:59:41  PC5 colour (65, 30, 20)
          TX ad0605411e14ffff29  RX -
01:59:41  PC5 white 50
          TX ad06043203ffffffe9  RX -
01:59:49  [cam] PC5 manual app-table (65, 30, 20, 50): ratio 1.598 1.590  (luma 179.0, ref 112.6)
01:59:49  PC5 colour (58, 21, 12)
          TX ad06053a150cffff11  RX -
01:59:49  PC5 white 32
          TX ad06042003ffffffd7  RX -
01:59:57  [cam] PC5 manual 09:00-point-at-10:00 (58, 21, 12, 32): ratio 1.437 1.439  (luma 157.4, ref 109.4)
01:59:57  PC5 time_sync 9:15
          TX ad0606090f0003ffd3  RX -
01:59:57  PC5 program(1)
          TX ad060801ffffffffb8  RX -
02:00:05  [cam] PC5 Dutch program at 9:15: ratio 1.449 1.451  (luma 159.2, ref 109.7)
02:00:05  PC5 colour (69, 26, 18)
          TX ad0605451a12ffff27  RX -
02:00:05  PC5 white 56
          TX ad06043803ffffffef  RX -
02:00:13  [cam] PC5 manual app-table (69, 26, 18, 56): ratio 1.602 1.596  (luma 179.6, ref 112.6)
02:00:13  PC5 colour (59, 23, 14)
          TX ad06053b170effff16  RX -
02:00:13  PC5 white 37
          TX ad06042503ffffffdc  RX -
02:00:21  [cam] PC5 manual 09:00-point-at-10:00 (59, 23, 14, 37): ratio 1.445 1.447  (luma 157.6, ref 108.9)
02:00:21  PC5 time_sync 9:30
          TX ad0606091e0003ffe2  RX -
02:00:21  PC5 program(1)
          TX ad060801ffffffffb8  RX -
02:00:29  [cam] PC5 Dutch program at 9:30: ratio 1.616 1.608  (luma 175.7, ref 109.2)
02:00:29  PC5 colour (74, 22, 15)
          TX ad06054a160fffff25  RX -
02:00:29  PC5 white 62
          TX ad06043e03fffffff5  RX -
02:00:37  [cam] PC5 manual app-table (74, 22, 15, 62): ratio 1.590 1.589  (luma 178.5, ref 112.4)
02:00:37  PC5 colour (61, 26, 16)
          TX ad06053d1a10ffff1d  RX -
02:00:37  PC5 white 41
          TX ad06042903ffffffe0  RX -
02:00:45  [cam] PC5 manual 09:00-point-at-10:00 (61, 26, 16, 41): ratio 1.577 1.576  (luma 175.6, ref 111.4)
02:00:45  PC5 time_sync real
          TX ad060602002d03ffea  RX -
02:00:45  PC5 scenario(7)
          TX ad060707ffffffffbd  RX -
02:00:45  PC5 color(0,100,0)
          TX ad0605006400ffff1a  RX -
02:00:46  PC5 white(0)
          TX ad06040003ffffffb7  RX -
02:00:46  PC5 final status
          TX bd0601ffffffffffbf  RX bd0a81015a000007006400000e  [status on bright=90 speed=0 manual RGBW=0,100,0,0]
```

