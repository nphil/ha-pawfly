# Pawfly (PinYing PY4C) aquarium light — Home Assistant integration spec (2026-09-30)

Binding for every build agent. Owner: orchestrator. Repo `/data/home/ha-pawfly` (git, branch main; agents
commit NOTHING). Future GitHub `nphil/ha-pawfly`, HACS custom repo. Domain **`pawfly`**, integration name
"Pawfly Aquarium Light".

## Hardware + app (verified)

- Light: Pawfly 6.5W submersible WRGB light with inline "BT-PF001" controller, made by Dongguan PinYing
  Electronics (品盈). Advertises as **`PY4C-BT-PSTL`** at `78:9C:E7:2D:FB:A3` (BLE, heard by Plant Room
  Bluetooth Proxy `54:32:04:3E:F3:72`, RSSI ~-54). App name prefixes: `PY4C`, `PYLamp-4C`
  (`BaseDevice.AdvertisedName`); display name = advertised name minus the prefix and leading `-`/`_`.
- Vendor app: "P-light controller" `com.aquapinyin.control` 1.1.2, .NET MAUI. Decompiled C# (ILSpy) in
  `research/decompiled/` (key files: `AquaPinyin.Services/BaseDevice.cs`, `FourChannel.cs`,
  `AquaPinyin.Models/Lamp4CTimer.cs`, `AquaPinyin.PageModels/*`, `AquaPinyin.Pages/Lamp4C*.cs`,
  XAML in `__XamlGeneratedCode__/`). String resources are in the satellite/main resources; the English
  resource DLL is `/data/home/tmp/pawfly/native/named/AquaPinyin.dll` (embedded `.resources`), satellites
  `AquaPinyin.resources.dll`. Tools: `export DOTNET_ROOT=$HOME/.dotnet PATH=$HOME/.dotnet:$HOME/.dotnet/tools:$PATH
  DOTNET_ROLL_FORWARD=Major; ilspycmd ...` (v9.1).
- In HA it will be named **"Plant Room Aquarium Accent Light"** (area plant_room) → entity ids
  `*.plant_room_aquarium_accent_light*`; name_curator shortens the display to "Aquarium Accent Light". Plant
  Room already has a Fluval `light.plant_room_aquarium_light` — never touch it.

## GATT

Service `0000ffe0-...-00805f9b34fb`; ONE characteristic `0000ffe1-...` used for both write (commands) and
notify (replies). Subscribe to notify FIRST, then authenticate.

## Frames (verified live 2026-09-30 through BlueShark raw writes)

All frames: `[family, len, cmd, payload..., checksum]`, checksum = sum(all previous bytes) & 0xFF
(the vendor's `% 65535` before `& 0xFF` is a no-op for these lengths). Family `0xAD` = set/command,
`0xBD` = query/auth; replies from the light start with `0xBD`. Unused payload bytes are `0xFF`.

| Purpose | Bytes (before checksum) | Notes |
|---|---|---|
| Verify key (auth) | `BD 06 0A k4 k3 k2 k1 FF` | key = 8 decimal digits, default `12345678` → `78 56 34 12` (digit pairs reversed, as BCD-like hex). **Required**: without it the light ignores queries (verified). Reply `BD 06 8A 01 ...` = OK, `8A 00` = wrong key. |
| Change key | `AD 06 20 k4 k3 k2 k1 FF` | same packing. App only after verify. |
| Rename | `AD len+1 21 <ascii name ≤16 bytes>` | len byte = name length + 1. App prefixes nothing; check SettingPageModel for exact name composition (it may keep the `PY4C-` prefix — preserve advertised prefix so discovery still matches!). |
| Query status | `BD 06 01 FF FF FF FF FF` | reply `BD 0A 81 power bright speed mode sel R G B W ..` (verified: `bd0a81015a000007006400000e` = on, 90 %, speed 0, mode 0 manual, sel 7, R0 G100 B0 W0). mode 0 manual, 1 scenario (sel = scenario 0-7), 2 timer (sel = program; ≥128 → sel-125 i.e. 3..5 = DIY 1-3). RGBW are 0-100 %. |
| Power | `AD 06 01 on FF FF FF FF` | on = 1/0. |
| Brightness | `AD 06 02 pct FF FF FF FF` | 1-100 (app slider). |
| Effect speed | `AD 06 03 v FF FF FF FF` | range from app UI. |
| White channel | `AD 06 04 w 03 FF FF FF` | chl byte 3 = white. (Test whether chl 0/1/2 set R/G/B individually = hidden feature.) |
| Colour | `AD 06 05 R G B FF FF` | 0-100 each. |
| Time sync | `AD 06 06 hh mm ss dow FF` | dow Mon=1..Sun=7. Light forgets time on power loss; app syncs on connect. |
| Scenario | `AD 06 07 idx FF FF FF FF` | 0-7 (8 built-in non-timer effects; app sets its mirror colours: 0 rgb30/w30, 1 all off, 2 all 100, 3 r0 g10 b100 w0, 4 r100 w100, 5 b100 w100, 6 w100, 7 rgb100 w0). Names/images from app resources. |
| Timer program | `AD 06 08 id FF FF FF FF` | id 0 Japanese, 1 Dutch, 2 Jungle, 128/129/130 = DIY 1/2/3 (app idx 3-5 + 125). |
| DIY upload header | `AD 06 09 id count 00 7F FF` | then `count` point frames: `AD 08 0A ord hh mm R G B W` (points sorted by time, ord from 0). |
| Demo | `AD 06 0B FF FF FF FF FF` | app "DEMO" button (see Lamp4CPageModel:799). |
| Live preview while editing DIY | `AD 06 0C 01 R G B W` / `AD 06 0C 00 00 00 00 00` (end) | hidden-ish: shows a colour immediately without changing mode. |
| Query DIY | `BD 06 02 id FF FF FF FF` | reply `BD .. 82 id count ..` then `count` × `BD .. 83 ord hh mm R G B W ..`. |

Presets (app-side curves, `Lamp4CTimer.Preset`; hh:mm R G B W): Japanese(0) 07:00 0000/07:15 70×4/08:00 70×4/
08:15 100×4/15:00 100×4/15:15 70×4/18:00 70×4/18:15 b100/22:00 b100/22:15 off. Dutch(1) 06:00 off/08:00
50,12,3,15/09:00 65,30,20,50/11:00 100,0,0,100/13:00 100×4/16:00 70,70,100,100/17:00 50×4/18:00 50,10,10,10/
19:00 0,50,100,0/22:00 off. Jungle(2) 08:00 off/08:30 100,50,10,10/09:00 100×4/17:00 100×4/18:00 100,50,15,0/
19:00 0,50,100,0/22:00 off. Between points the firmware presumably fades (verify).

## Integration contract (backend)

- Config flow: Bluetooth discovery (`local_name` `PY4C*` and `PYLamp-4C*`, service ffe0) + user step picking
  a discovered light; asks for the 8-digit password (default 12345678) and verifies it live before creating the
  entry; unique_id = address. Options (OptionsFlowWithReload): keep_connected (default True), status poll
  seconds (default 60). Reauth flow on wrong key.
- Connection: persistent held link through ESPHome proxies following fluvalble's proven patterns
  (`/data/home/fluvalble/custom_components/fluvalble/`: serialized GATT ownership, backoff, `ble_affinity.py`
  preferred-proxy affinity — vendor it, keep its header noting the vendored copies, outage clocks outside the
  coordinator) and the house skill `ha-persistent-ble-integration`. On every (re)connect: subscribe → verify key
  → sync time → query status. Re-sync time daily at 03:17 local and after any reconnect. After every command:
  query status (debounced) so state reflects the device. Parse unsolicited notifications too.
- Entities (translation keys, `has_entity_name`, device name from entry title):
  - `light` (main): on/off, brightness (HA 1-255 ↔ 1-100 %), ColorMode.RGBW (0-100 % per channel ↔ 0-255),
    effect list = the 8 scenario names + "Demo". Setting a colour/white while in scenario/timer mode puts the
    light in manual mode (check what firmware does; mirror app behaviour).
  - `select` `program`: Manual, Japanese style, Dutch style, Jungle style, DIY 1, DIY 2, DIY 3 (reflects mode;
    selecting Manual restores last manual colour).
  - `number` `effect_speed` (app range).
  - `sensor` `mode` (enum manual/scene/program) — diagnostic, and `binary_sensor` `connected` (diagnostic).
  - `button` `sync_time` (EntityCategory.CONFIG → disabled by default per house rule 4), `button` `release_link`
    (diagnostic, enabled).
- Services (`services.yaml`, response where noted): `pawfly.get_program {device_id, program}` → points
  (response); `pawfly.set_program {device_id, program: DIY 1-3, points:[{time:"HH:MM", red, green, blue,
  white}]}` (validate ≤ app max points, unique sorted times, 0-100) → writes then reads back to verify;
  `pawfly.preview {device_id, red, green, blue, white | end: true}`; `pawfly.sync_time`; `pawfly.rename {name}`;
  `pawfly.change_password {new_password}` (updates entry data after success); `pawfly.release_link`.
- Diagnostics download (redact password). Brand assets (8 PNGs), icons.json, strings/translations.
- Tests: pure protocol golden tests (every encoder vs vendor-derived bytes, parser, DIY assembly), real-HA
  harness via `pytest-homeassistant-custom-component` (reuse `/data/home/ha-iledclock/.venv-ha` — Python 3.14,
  HA 2026.9.3) with a fake BLE client.

## Card contract (frontend)

Lovelace card `pawfly-card` shipped by the integration (served from `custom_components/pawfly/frontend/`, auto-
registered like iledclock's), Lit + TS + esbuild, Lucent design (`/data/agent/managed-skills/design-language`:
host HA theme colours, 48 px targets, container queries, 320 px → wide, dark/light, flat + glass themes,
reduced motion). Hero = a calm "tank glow" visual of the current RGBW mix at current brightness with
status chips (Connected, mode). Segmented modes: **Colour** (RGBW sliders + brightness, swatch presets),
**Effects** (8 scenario tiles with the app's names/colours + Demo + speed), **Schedule** (program picker with
a 24 h curve chart per channel for the 3 presets and DIY 1-3; DIY editor: add/drag/delete points on the
timeline, per-point RGBW, live preview on the light via `pawfly.preview` while dragging with end on close,
Save = hold-to-confirm (overwrites the light's program), Activate = tap). Card editor: entity picker.

## Hidden features to look for

The app only exposes some opcodes. Look for firmware capabilities the app hides (e.g. per-channel `0x04` with chl
0-2, brightness inside timer mode, more than 8 scenarios, query opcodes `BD 06 03..` returning firmware
version/model). **Only sweep query (0xBD) opcodes blindly**; never blind-sweep 0xAD set opcodes (could reset/
brick). Every write test restores the original state afterwards.
