# Vendor app feature catalogue — "P-light controller" 1.1.2

Every feature of the vendor app for the Pawfly / PinYing PY4C light, with where it comes from in the decompiled
code. Paths are relative to `research/decompiled/` (ILSpy output of `AquaPinyin.dll`; not committed, see
`docs/SPEC.md`) and written `file:line`. Wire-level details of the frames are in [PROTOCOL.md](PROTOCOL.md).

Strings come from the embedded resources of `AquaPinyin.dll` (English, neutral culture) and the two satellite
assemblies `zh-Hans/AquaPinyin.resources.dll` and `zh-Hant/AquaPinyin.resources.dll` (all three read with `dnfile`
and exported with `ilspycmd`; the English export is `AquaPinyin.Resources.AppResources.resx`). Text that is not in
the resources but hard-coded in the pages is listed separately in section 11.

## 1. Identity

| Item | Value | Source |
|---|---|---|
| Name / package / version | "P-light controller", `com.aquapinyin.control`, 1.1.2 (code 5), Android 7+ (SDK 24), .NET MAUI | `xapk/manifest.json` of the APK bundle |
| Permissions | Bluetooth, Bluetooth scan/connect, fine + coarse location | same |
| Orientation | portrait only | `AquaPinyin/MainActivity.cs:20` |
| Supported lights | Bluetooth names starting `PY4C` or `PYLamp-4C`; both open the same 4-channel page | `AquaPinyin.Services/BaseDevice.cs:16`, `AquaPinyin.PageModels/HomePageModel.cs:247-252` |
| GATT | service `0xFFE0`, one characteristic `0xFFE1` for read, write and notify | `AquaPinyin.Services/FourChannel.cs:42` |
| Channels | red, green, blue, white (`FourChannel`) | `AquaPinyin.Services/FourChannel.cs:13-31` |

## 2. Navigation shell

| Element | Text / behaviour | Source |
|---|---|---|
| Flyout header | "P-LIGHT CONTROL" | `AquaPinyin.Pages.Controls/FlyoutHeader.cs:46` |
| Flyout items | "LIGHT MODULES" (route `Home`, icon `ico_ble`) and "GETTING STARTED" (route `Guide`, icon `ico_guide`) | `AquaPinyin/AppShell.cs:96-105` |
| Flyout footer | "Version: <app version>", "Powered by .Net MAUI, CLR: …", underlined "Privacy Policy" link to `http://www.aquapinyin.cn/privacypolicy.html` | `AquaPinyin.Pages.Controls/FlyoutFooter.cs:27-33` |
| Routes | `Home/4CLamp` = the light page, `Home/4CLamp/Timers` = program list | `AquaPinyin/AppShell.cs:26-27` |

## 3. Home / scan page ("LIGHT MODULES")

| Feature | Detail | Source |
|---|---|---|
| Title, prompt | title "LIGHT MODULES"; prompt "Please Choose a Module:" | `AquaPinyin.PageModels/HomePageModel.cs:189`, `AquaPinyin.Pages/HomePage.cs:527` |
| Banner | one of `banner0.png`…`banner9.png` chosen at random each start | `AquaPinyin.PageModels/HomePageModel.cs:272-276` |
| Scan | button "SEARCH" → "Searching…" (disabled) → "SEARCH AGAIN"; empty text "No devices found."; scan runs 5 s in balanced mode | `AquaPinyin.PageModels/HomePageModel.cs:192-222`, `UnSign.Maui.BLE/BleDevice.cs:53-54` |
| Filter | keeps devices whose name *starts with* `PY4C` or `PYLamp-4C` (no service-UUID filter) | `AquaPinyin.PageModels/HomePageModel.cs:321`, `UnSign.Maui.BLE/BleDevice.cs:71-89` |
| List | newest hit first; each row shows icon `dev_pylamp_4c` and the display name (advertised name minus prefix and leading `-`/`_`) | `AquaPinyin.PageModels/HomePageModel.cs:324`, `AquaPinyin.PageModels/ScanDevice.cs:12-25`, `AquaPinyin.Services/BaseDevice.cs:49-61` |
| Rescan | automatic on first show, when the list is empty, and after a rename (`App.renamed`) | `AquaPinyin.PageModels/HomePageModel.cs:301-305` |
| Bluetooth off | snackbar "Bluetooth is not available, please turn on Bluetooth in the system settings first." | `AquaPinyin.PageModels/HomePageModel.cs:347-351` |
| Permissions | Android ≤ 11 asks for location, newer versions for Bluetooth scan/connect; explanation "Description of permissions for location or connecting nearby devices" / "Used to find and connect to nearby Bluetooth devices." | `AquaPinyin.PageModels/HomePageModel.cs:330-346`, `AquaPinyin.Pages/HomePage.cs:513-515` |
| Privacy prompt | first start on Android: "Privacy prompt" — "Please read and agree carefully before using the app." with "Agree" / "Disagree and exit"; the answer is kept in preferences | `AquaPinyin.PageModels/HomePageModel.cs:277-300`, `AquaPinyin.Pages.Controls/PPDialog.cs:44-102`, `AquaPinyin.Utilities/AppStorage.cs:62-72` |
| Tap a device | asks "AUTHENTICATION" / "Please enter an eight-digit PIN for connecting the device (<name>)" (numeric, hidden, 8 characters, placeholder "8 numerical digits") unless a PIN is stored for that device; then opens the light page | `AquaPinyin.PageModels/HomePageModel.cs:224-267` |

## 4. Guide page ("GETTING STARTED")

English text in `AquaPinyin.Pages.Controls/Guide_1.cs:71-170`; the page picks the Traditional-Chinese, Simplified-Chinese or English
guide by UI culture (`AquaPinyin.Pages/GuidePage.cs:23-35`). Content:

- Every light has a factory preset Bluetooth name and PIN; select the device on the Home screen and enter the PIN
  (first time: the one in the manual or on the silver sticker). "If you device does not appear on the Home screen,
  check to make sure the device has power." (`Guide_1.cs:71-99`)
- **Factory reset**: press the controller's **Hour +** and **Minute −** buttons (icons `doc_btn1.png`, `doc_btn2.png`)
  together for 3 seconds; this resets the unit to factory settings. (`Guide_1.cs:121-143`; the Chinese guide adds that this
  is the way out when the PIN is forgotten, `Guide_2.cs:125-143`.)
- **Initial PIN: 12345678** (`Guide_1.cs:170`).

## 5. Connecting

| Step | Behaviour | Source |
|---|---|---|
| Connect | 12 s timeout, status text "CONNECTING…"; failure: "CONNECTION FAILED" and toast "Failure to connect device!" + reason | `AquaPinyin.PageModels/DevicePageModel.cs:152-196` |
| Link options | no auto-connect, no forced transport | `UnSign.Maui.BLE/BleDevice.cs:130` |
| After connect | start notifications on `0xFFE1`, then send the key ("VERIFY PIN…") | `AquaPinyin.PageModels/DevicePageModel.cs:272-282`, `AquaPinyin.Services/BaseDevice.cs:104-117` |
| Key accepted | PIN stored per device, status queried once, then the clock is set (once per connection) | `AquaPinyin.PageModels/DevicePageModel.cs:290-328`, `AquaPinyin.PageModels/Lamp4CPageModel.cs:691-694`, `AquaPinyin.Services/FourChannel.cs:382-401` |
| Key refused | stored PIN deleted, link dropped, back to the list, toast "Failure to authenticate, device refuses to connect!" | `AquaPinyin.PageModels/DevicePageModel.cs:314-316` |
| Disconnect | status reset (colours cleared, mode 0) and the page shows "NOT CONNECTED" | `AquaPinyin.Services/BaseDevice.cs:189-194`, `AquaPinyin.Services/FourChannel.cs:46-56` |

The app never polls the status: it asks once after authentication and afterwards only parses whatever the light notifies
(`AquaPinyin.PageModels/DevicePageModel.cs:319-328`).

## 6. Light page (`Lamp4CPage`)

### 6.1 Header and power

- Title = status text: "NOT CONNECTED", "OFF", "ON" or, while on, the mode: "CUSTOMIZE" (manual), "SCENE", "TIMING"
  (`AquaPinyin.PageModels/DevicePageModel.cs:242-266`, `AquaPinyin.PageModels/Lamp4CPageModel.cs:112-136`, `AquaPinyin.Pages/Lamp4CPage.cs:691-705`).
- Toolbar "MENU" (icon `ico_more`) opens the settings page, help opens the guide (`AquaPinyin.Pages/Lamp4CPage.cs:706-744`,
  `AquaPinyin.PageModels/DevicePageModel.cs:125-141`).
- Round power button (icons `ico_power_on` / `ico_power_off`). The app **toggles** relative to what it last read: it sends
  `on = not currentPower` (`AquaPinyin.PageModels/DevicePageModel.cs:143-150`, `AquaPinyin.Services/FourChannel.cs:134-164`).
- Colour, brightness and scenario controls are disabled while the light is off or not connected
  (`AquaPinyin.PageModels/Lamp4CPageModel.cs:620`, `AquaPinyin.Pages/Lamp4CPage.cs:883-897`).
- Two bottom tabs: **Basic** (icon `ico_tab_basic`) and **Pro.** (icon `ico_tab_pro`)
  (`AquaPinyin.Pages/Lamp4CPage.cs:2232, 2279`, labels `AquaPinyin.Pages/Lamp4CPage.cs:419-423`).

### 6.2 Basic tab

**Brightness**: slider from **1 to 100** with a dark bulb (`ico_bulb_r`) and a bright bulb (`ico_bulb_f`) at its ends and the label
"Brightness: N" (the code also has "Brightness: OFF" for 0, which the slider cannot reach)
(`AquaPinyin.Pages/Lamp4CPage.cs:231-243, 807, 857-858, 900`). It changes no mode.

**Colour palette** (`AquaPinyin.Pages.Controls/Palette.cs`):

- a colour picker in "hue to tint" style (spectrum with hue along one axis and white tint along the other, no wheel)
  (`Palette.cs:333-334`);
- four sliders, each **0–100 %**: Red, Green, Blue and White, labelled "Red: 37%" etc. (`Palette.cs:82-95, 391-392, 446-447, 501-502, 556-557`);
- picking a colour or moving a slider switches the light to manual mode (`AquaPinyin.Pages/Lamp4CPage.cs:211-229`);
  the colour is converted to percent by *truncating* `value * 100f` (`AquaPinyin.Utilities/ColorExtension.cs:15-22`), which sends 52
  instead of 53 and 58 instead of 59 for those two slider positions (measured with the vendor arithmetic,
  `tests/fixtures/vendor_golden.json` → `vendor_quirks`).

**Scenarios** (8 tiles, a 4 × 2 grid; choosing one switches to scenario mode)
(`AquaPinyin.Pages/Lamp4CPage.cs:1263-1508`, index mapping `AquaPinyin.PageModels/Lamp4CPageModel.cs:722-762`):

| Index | Tile | Label / icon | Colours the app mirrors on its palette (R G B W %) | What the light does (camera, live) |
|---|---|---|---|---|
| 0 | row 1, col 1 | icon `sc1.png`: sun behind a cloud | 30 30 30 30 | steady, medium-bright |
| 1 | row 1, col 2 | icon `sc2.png`: cloud with lightning bolt | 0 0 0 0 | animated, brightness keeps changing (storm) |
| 2 | row 1, col 3 | icon `sc3.png`: sun | 100 100 100 100 | steady, bright |
| 3 | row 1, col 4 | icon `sc4.png`: crescent moon | 0 10 100 0 | steady, dim blue |
| 4 | row 2, col 1 | text "Warm White" | 100 0 0 100 | steady, bright (red + white) |
| 5 | row 2, col 2 | text "Bright White" | 0 0 100 100 | steady, bright (blue + white) |
| 6 | row 2, col 3 | text "Day" | 0 0 0 100 | steady, bright (white only) |
| 7 | row 2, col 4 | text "RGB Cycle" | 100 100 100 0 | animated, colours cycle |

The mirror colours come from `AquaPinyin.Services/FourChannel.cs:284-318`; the tiles use a light-grey icon and a white one when
selected (`sc*.png` / `sc*_p.png`, `AquaPinyin.Pages.Controls/SelectImageButton.cs:9-50`). The four icon-only tiles have no name anywhere in
the app; `protocol.SCENARIO_NAMES` names them after their icons ("Cloudy", "Thunderstorm", "Sunny", "Moonlight"). Chinese
strings: Warm White 暖白光, Bright White 冷白光, Day 純白光, RGB Cycle 七彩循環 (section 10).

There is **no speed control** anywhere: `AdjSpeed` exists (`AquaPinyin.Services/FourChannel.cs:251-276`) but no page calls it.

### 6.3 Pro tab

- Header "24/7 Mode Selection" with six select buttons in this order: "Japanese style", "Dutch style", "Jungle style", "DIY 1", "DIY 2",
  "DIY 3" (`AquaPinyin.Pages/Lamp4CPage.cs:349, 1611-1819`). Choosing one switches to timer (program) mode and sends the program
  id (`AquaPinyin.PageModels/Lamp4CPageModel.cs:764-795`, `AquaPinyin.Services/FourChannel.cs:340-365`).
- **DEMO** button, enabled only while the mode is timer/program (`AquaPinyin.PageModels/Lamp4CPageModel.cs:121, 548-559, 796-800`,
  frame `AquaPinyin.Services/FourChannel.cs:367-380`); there is no stop button (`STOP` exists in the resources but is never used).
- "View" section: Japanese / Dutch / Jungle buttons open the read-only program list; "Edit" section: DIY 1 / DIY 2 / DIY 3 open
  the editable list (`AquaPinyin.Pages/Lamp4CPage.cs:180-209, 1951-2004, 2108-2157`).

### 6.4 How changes reach the light

A 0.25 s timer runs while the page is visible and pushes only what differs from the last read state, one command per tick
(`AquaPinyin.PageModels/Lamp4CPageModel.cs:570-602, 696-802`): brightness; in manual mode white (re-sent every time the page is
entered) and colour; in scenario mode the scenario, after which the palette is set to the scenario's mirror colours; in timer mode
the program. Commands are separate frames and are not acknowledged by the light.

## 7. Program pages (`Lamp4CTimersPage`, `Lamp4CTimerPage`)

### 7.1 The three built-in styles (read-only "View")

Title "Japanese style" / "Dutch style" / "Jungle style", description "Check the parameters of the {0}". The list shows, per point,
`HH:mm`, White, Red, Green, Blue as percent (`AquaPinyin.Pages/Lamp4CTimersPage.cs:449-522, 598-603`). The values are a table inside the
app, not read from the light (`AquaPinyin.Models/Lamp4CTimer.cs:107-149`), and the light does not answer a query for them:

| Style | Points (time R G B W %) |
|---|---|
| Japanese | 07:00 0 0 0 0 · 07:15 70 70 70 70 · 08:00 70 70 70 70 · 08:15 100 100 100 100 · 15:00 100 100 100 100 · 15:15 70 70 70 70 · 18:00 70 70 70 70 · 18:15 0 0 100 0 · 22:00 0 0 100 0 · 22:15 0 0 0 0 |
| Dutch | 06:00 0 0 0 0 · 08:00 50 12 3 15 · 09:00 65 30 20 50 · 11:00 100 0 0 100 · 13:00 100 100 100 100 · 16:00 70 70 100 100 · 17:00 50 50 50 50 · 18:00 50 10 10 10 · 19:00 0 50 100 0 · 22:00 0 0 0 0 |
| Jungle | 08:00 0 0 0 0 · 08:30 100 50 10 10 · 09:00 100 100 100 100 · 17:00 100 100 100 100 · 18:00 100 50 15 0 · 19:00 0 50 100 0 · 22:00 0 0 0 0 |

### 7.2 DIY 1–3 (editable)

| Feature | Detail | Source |
|---|---|---|
| Load | when connected, asks the light for the program (`BD 06 02 id`); a spinner shows until the reply is complete; the list is sorted by time; not connected → nothing is shown | `AquaPinyin.Pages/Lamp4CTimersPage.cs:634-659, 670-682`, `AquaPinyin.Services/FourChannel.cs:475-494` |
| Empty slot | an empty list (the app's local default list of ten points at 06, 08, 10, … 22, 00 h is dead code: nothing ever loads or saves DIY programs locally) | `AquaPinyin.Models/Lamp4CTimer.cs:62-105`, `AquaPinyin.Pages/Lamp4CTimersPage.cs:648-652` |
| Description | "Set the timing and brightness of the {0}. Swipe left to delete the timer" | `AquaPinyin.Pages/Lamp4CTimersPage.cs:598` |
| Maximum | **12 points**: "Up to 12 timers can be set." | `AquaPinyin.Pages/Lamp4CTimersPage.cs:691, 705` |
| Add | toolbar "+" (`ico_add`); a new point starts at the current `HH:mm` with grey colour and white 0 | `AquaPinyin.Pages/Lamp4CTimersPage.cs:684-706, 850` |
| Delete | swipe left, red "REMOVE" | `AquaPinyin.Pages/Lamp4CTimersPage.cs:330-415, 609-615` |
| Edit a point | tap a row: modal "Timer details" with a time picker (`HH:mm`, so **1-minute steps**), the same palette as the light page (R, G, B, W 0–100 %) and OK / Cancel | `AquaPinyin.Pages/Lamp4CTimerPage.cs:57-100, 131-174, 421` |
| Duplicates | two points at the same minute are refused: "A timer for {0} already exists." (checked when confirming a point and again when saving) | `AquaPinyin.Pages/Lamp4CTimerPage.cs:138-167`, `AquaPinyin.Pages/Lamp4CTimersPage.cs:719-728` |
| Live preview | while the editor is open, a 0.5 s timer sends the current colour with the *preview* frame (`AD 06 0C 01 R G B W`); closing the editor sends the *end* frame (`AD 06 0C 00 …`) | `AquaPinyin.Pages/Lamp4CTimerPage.cs:67-90, 102-129`, `AquaPinyin.Services/FourChannel.cs:451-473` |
| Save | button "SAVE": sorts by time and writes a header (id, count) followed by one frame per point (order 0…n-1, `hh mm R G B W`); closes the page when every frame was written | `AquaPinyin.Pages/Lamp4CTimersPage.cs:708-741`, `AquaPinyin.Services/FourChannel.cs:403-449` |
| Brightness per point | not sent. The editor text "Sets the brightness after starting this timer" is a leftover: the point frame carries only R, G, B, W and the editor has no brightness control | `AquaPinyin.Pages/Lamp4CTimerPage.cs:64`, `AquaPinyin.Services/FourChannel.cs:430-436` |

Wire format of a program: header `AD 06 09 id count 00 7F FF`, then `AD 08 0A order hh mm R G B W` per point (see PROTOCOL.md).

## 8. Settings page (`SettingPage`, "LOGIN SETTINGS")

Reached from the light page's MENU while connected. Title "LOGIN SETTINGS", intro "Update your light module's settings below."

| Field | Behaviour | Source |
|---|---|---|
| Name ("NAME:", icon `ico_tag`) | shows the **display name** (prefix removed); "This will be the name used to identity your module in your device's Bluetooth settings menu."; hint "Letters or numbers"; at most **16 characters** | `AquaPinyin.PageModels/SettingPageModel.cs:127-133`, `AquaPinyin.Services/BaseDevice.cs:42-47`, `AquaPinyin.Pages/SettingPage.cs:288-308` |
| PIN ("PIN:", icon `ico_sec`) | pre-filled with the PIN stored for this light; numeric keyboard, hidden, 8 characters max, placeholder "8 numerical digits"; "To change the PIN of the module, enter up to eight numerical digits only. This will be the PIN required to connect your device to the smart module once saved." | `AquaPinyin.PageModels/SettingPageModel.cs:132`, `AquaPinyin.Pages/SettingPage.cs:479-482` |
| Save rules | enabled when connected, name not blank and ≤ 16 characters, and the PIN either blank or **exactly 8 digits** (`0-9` only) | `AquaPinyin.PageModels/SettingPageModel.cs:183-198`, `AquaPinyin.Services/BaseDevice.cs:78-92` |
| Save flow | confirm dialog "After changing the settings, you may need to disconnect and re-search and reconnect before it takes effect. Sure to commit changes to the device?"; if the PIN changed, send *change key* (and forget the stored PIN); then send *rename*; success → back, failure → toast "Save failed！…" | `AquaPinyin.PageModels/SettingPageModel.cs:135-181` |
| Rename frame | `AD n+1 21 <name bytes>`; the name goes out **without** the `PY4C` prefix, exactly as displayed; longer than 16 characters throws "Name is too long"; bytes are cut to 16 | `AquaPinyin.Services/BaseDevice.cs:119-160` |
| Prefix | the app scans for `PY4C…` again after a rename (`App.renamed` triggers a rescan), so the light must keep its prefix itself; the Chinese guide even says renamed devices are found by their new name | `AquaPinyin.PageModels/SettingPageModel.cs:159-162`, `AquaPinyin.PageModels/HomePageModel.cs:301-305`, `AquaPinyin.Pages.Controls/Guide_2.cs:97` |
| Quirk | `DeviceName != _device.Name` compares the display name with the full advertised name, so *Save always sends a rename*, even when the name was not touched | `AquaPinyin.PageModels/SettingPageModel.cs:159` |

Key frame: 8 digits `k0…k7` are padded on the right with `0` if short, then sent as the bytes `k6k7 k4k5 k2k3 k0k1` after `BD 06 0A`
(verify) or `AD 06 20` (change) plus `FF` (`AquaPinyin.Services/BaseDevice.cs:206-240`).

## 9. Time, power and status

| Function | Behaviour | Source |
|---|---|---|
| Time sync | phone local time as `hh mm ss dow` (Monday 1 … Sunday 7), sent **once per connection** right after the first status | `AquaPinyin.Services/FourChannel.cs:382-401`, `AquaPinyin.PageModels/Lamp4CPageModel.cs:691-694` |
| Power | toggles relative to the last known state (see 6.1); local state is flipped after the write succeeded | `AquaPinyin.Services/FourChannel.cs:134-164` |
| Status reply | `BD 0A 81 power brightness speed mode selector R G B W cs`; mode 0 manual / 1 scenario / 2 timer; selector ≥ 128 means DIY (minus 125) | `AquaPinyin.Services/FourChannel.cs:58-91` |
| Program reply | `BD 06 82 id count …` then `count` × `BD 08 83 order hh mm R G B W`; the app adds points in arrival order | `AquaPinyin.Services/FourChannel.cs:92-124` |
| Key reply | `BD 06 8A 01` accepted, anything else refused | `AquaPinyin.Services/BaseDevice.cs:250-254` |

## 10. English UI strings (all 75 resources)

Read from the embedded resources; "used at" is the first place the key is referenced. Keys without a use are marked.

All lines of the English column are line numbers in `AquaPinyin.Resources.AppResources.resx`.

| Key | resx line | English | 简体中文 (zh-Hans) | 繁體中文 (zh-Hant) | used at |
|---|---|---|---|---|---|
| `WHITE_BRIGHT` | 30 | Bright White | 冷白光 | 冷白光 | AquaPinyin.Pages/Lamp4CPage.cs:331 |
| `PASSWORD` | 31 | PASSWORD | 密码 | 密碼 | (unused) |
| `SETTINGS_CHANGE` | 32 | After changing the settings, you may need to disconnect and re-search and reconnect before it takes effect.{0}Sure to commit changes to the device? | 更改设置后，您可能需要断开连接并重新搜索再连接，然后才会生效。{0}确定要提交更改到设备？ | 更改設置后，您可能需要斷開連接並重新搜索再連接，然後才會生效。 {0}確定要提交更改到設備？ | AquaPinyin.PageModels/SettingPageModel.cs:138 |
| `ACCEPT` | 33 | Agree | 同意 | 同意 | AquaPinyin.Pages.Controls/PPDialog.cs:54 |
| `NOT_CONNECTED` | 34 | NOT CONNECTED | 未连接 | 未連接 | AquaPinyin.PageModels/Lamp4CPageModel.cs:682, AquaPinyin.PageModels/DevicePageModel.cs:33 |
| `CANCEL` | 35 | Cancel | 取消 | 取消 | AquaPinyin.PageModels/HomePageModel.cs:243, AquaPinyin.PageModels/SettingPageModel.cs:138 |
| `L8_NUM` | 36 | 8 numerical digits | 8 位数字 | 8 位數字 | AquaPinyin.PageModels/HomePageModel.cs:243, AquaPinyin.Pages/SettingPage.cs:75 |
| `LS_NUM` | 37 | Letters or numbers | 字母或数字 | 字母或數字 | AquaPinyin.Pages/SettingPage.cs:59 |
| `PERMISSION_TITLE` | 38 | Description of permissions for location or connecting nearby devices | 位置或连接附近设备权限使用说明 | 位置或連接附近設備權限使用說明 | AquaPinyin.Pages/HomePage.cs:513 |
| `REMOVE` | 39 | REMOVE | 移除 | 移除 | AquaPinyin.Pages/Lamp4CTimersPage.cs:330 |
| `GET_STARTED` | 40 | GETTING STARTED | 使用指南 | 使用指南 | AquaPinyin/AppShell.cs:52 |
| `PERMISSION_REQ` | 41 | Permission required | 需要权限 | 需要許可 | (unused) |
| `PERMISSION_DEC` | 42 | Used to find and connect to nearby Bluetooth devices. | 用于查找与连接附近的蓝牙设备。 | 用於查找與連接附近的藍牙設備。 | AquaPinyin.Pages/HomePage.cs:515 |
| `SEARCH` | 43 | SEARCH | 搜索 | 搜尋 | AquaPinyin.PageModels/HomePageModel.cs:50 |
| `SEARCHING` | 44 | Searching... | 搜索中... | 搜尋中... | AquaPinyin.PageModels/HomePageModel.cs:200 |
| `BLUETOOTH_NOT_AVAILABLE` | 45 | Bluetooth is not available, please turn on Bluetooth in the system settings first. | 蓝牙当前不可用，请先在系统设置中打开蓝牙。 | 藍牙當前不可用，請先在系統設置中打開藍牙。 | AquaPinyin.PageModels/HomePageModel.cs:349 |
| `TIMER_DESC` | 46 | Sets the brightness after starting this timer | 设置启动此计时器后的亮度和色彩 | 設置啟動此計時器后的亮度和色彩 | AquaPinyin.Pages/Lamp4CTimerPage.cs:64 |
| `TIMER_EXISTS` | 47 | A timer for {0} already exists. | {0} 的定时器已经存在 | {0} 的定時器已經存在 | AquaPinyin.Pages/Lamp4CTimerPage.cs:143 |
| `PLEASE_ENTER_PASSWD` | 48 | Please enter an eight-digit PIN for connecting the device ({0}) | 请输入用于连接设备的八位数 PIN 码 （{0}） | 請輸入用於連接設備的八位數 PIN 碼 （{0}） | AquaPinyin.PageModels/HomePageModel.cs:243 |
| `PRIVACY_POLICY` | 49 | Privacy Policy | 隐私政策 | 隱私政策 | AquaPinyin.Pages.Controls/PPDialog.cs:49, AquaPinyin.Pages.Controls/FlyoutFooter.cs:46 |
| `LOGIN_DESC` | 50 | Update your light module's settings below. | 以下设置您的灯光设备。 | 以下設置您的燈光設備。 | AquaPinyin.Pages/SettingPage.cs:45 |
| `TIMER_DETAIL` | 51 | Timer details | 定时器详情 | 定時器詳情 | AquaPinyin.Pages/Lamp4CTimerPage.cs:63 |
| `MODE_SCENE` | 52 | SCENE | 场景模式 | 場景模式 | AquaPinyin.PageModels/Lamp4CPageModel.cs:127 |
| `MODE_CUSTOMIZE` | 53 | CUSTOMIZE | 自定义模式 | 自訂模式 | AquaPinyin.PageModels/Lamp4CPageModel.cs:128 |
| `VERIFY_PASSWORD` | 54 | VERIFY PIN... | 验证 PIN 码... | 驗證 PIN 碼... | AquaPinyin.PageModels/DevicePageModel.cs:187 |
| `OK` | 55 | OK | 确定 | 確定 | AquaPinyin.PageModels/HomePageModel.cs:243, AquaPinyin.PageModels/SettingPageModel.cs:138 |
| `ON` | 56 | ON | 开启 | 開啟 | AquaPinyin.PageModels/DevicePageModel.cs:263 |
| `BRIGHTNESS` | 57 | Brightness | 总亮度 | 總亮度 | AquaPinyin.Pages/Lamp4CPage.cs:237 |
| `CONNECTION_FAILED` | 58 | CONNECTION FAILED | 连接失败 | 連接失敗 | AquaPinyin.PageModels/DevicePageModel.cs:193 |
| `NAME_DESC` | 59 | This will be the name used to identity your module in your device's Bluetooth settings menu. | 用于标识您灯光设备的蓝牙名称。 | 用於標識您燈光設備的藍牙名稱。 | AquaPinyin.Pages/SettingPage.cs:63 |
| `ACCEPT_DISEXIT` | 60 | Disagree and exit | 不同意并退出 | 不同意並退出 | AquaPinyin.Pages.Controls/PPDialog.cs:56 |
| `DAY` | 61 | Day | 纯白光 | 純白光 | AquaPinyin.Pages/Lamp4CPage.cs:334 |
| `OFF` | 62 | OFF | 关闭 | 關閉 | AquaPinyin.PageModels/Lamp4CPageModel.cs:133, AquaPinyin.PageModels/DevicePageModel.cs:256 |
| `RED` | 63 | Red | 红色 | 紅色 | AquaPinyin.Pages/Lamp4CTimersPage.cs:764, AquaPinyin.Pages.Controls/Palette.cs:84 |
| `PRO` | 64 | Pro. | 定时 | 定時 | AquaPinyin.Pages/Lamp4CPage.cs:423 |
| `ERROR` | 65 | Error | 错误 | 錯誤 | AquaPinyin.Utilities/ModalErrorHandler.cs:15 |
| `GREEN` | 66 | Green | 绿色 | 綠色 | AquaPinyin.Pages/Lamp4CTimersPage.cs:767, AquaPinyin.Pages.Controls/Palette.cs:88 |
| `BASIC` | 67 | Basic | 基础 | 基礎 | AquaPinyin.Pages/Lamp4CPage.cs:419 |
| `CLOSE` | 68 | Close | 关闭 | 關閉 | AquaPinyin.Utilities/ModalErrorHandler.cs:15, AquaPinyin/AppShell.cs:38 |
| `L24_7` | 69 | 24/7 Mode Selection | 定时循环模式选择 | 定時循環模式選擇 | AquaPinyin.Pages/Lamp4CPage.cs:349 |
| `WHITE` | 70 | White | 白色 | 白色 | AquaPinyin.Pages/Lamp4CTimersPage.cs:761, AquaPinyin.Pages.Controls/Palette.cs:179 |
| `TIMERS_DESC_1` | 71 | Check the parameters of the {0} | 查看 {0} 的定时参数 | 查看 {0} 的定時參數 | AquaPinyin.Pages/Lamp4CTimersPage.cs:602 |
| `TIMERS_DESC_2` | 72 | Set the timing and brightness of the {0}. Swipe left to delete the timer | 设置 {0} 的定时和亮度。向左滑动可删除定时器 | 設置 {0} 的定時和亮度。 向左滑動可刪除定時器 | AquaPinyin.Pages/Lamp4CTimersPage.cs:598 |
| `SAVE_FAILED` | 73 | Save failed！{0} | 保存失败！{0} | 保存失敗！ {0} | AquaPinyin.PageModels/SettingPageModel.cs:175 |
| `RGB_CYCLE` | 74 | RGB Cycle | 七彩循环 | 七彩循環 | AquaPinyin.Pages/Lamp4CPage.cs:337 |
| `PLEASE_CHOOSE_MODULE` | 75 | Please Choose a Module: | 请选择一个模块 | 請選擇一個模組 | AquaPinyin.Pages/HomePage.cs:527 |
| `STYLE_JUNGLE` | 76 | Jungle style | 丛林风格 | 叢林風格 | AquaPinyin.Pages/Lamp4CTimersPage.cs:585, AquaPinyin.Pages/Lamp4CPage.cs:359 |
| `PASSWORD_LB` | 77 | PIN: | PIN 码： | PIN 碼： | AquaPinyin.Pages/SettingPage.cs:66 |
| `STYLE_JANAN` | 78 | Japanese style | 日本风格 | 日本風格 | AquaPinyin.Pages/Lamp4CTimersPage.cs:583, AquaPinyin.Pages/Lamp4CPage.cs:353 |
| `STYLE_DUTCH` | 79 | Dutch style | 荷兰风格 | 荷蘭風格 | AquaPinyin.Pages/Lamp4CTimersPage.cs:584, AquaPinyin.Pages/Lamp4CPage.cs:356 |
| `PP_ACCEPT` | 80 | Please read and agree carefully before using the app.  | 请使用 App 前仔细阅读并同意 | 請使用 App 前仔細閱讀並同意 | AquaPinyin.Pages.Controls/PPDialog.cs:46 |
| `PASSWORD_DESC` | 81 | To change the PIN of the module, enter up to eight numerical digits only. This will be the PIN required to connect your device to the smart module once saved. | 更改灯光设备的验证 PIN 码，最多只能输入八位数字。连接到该设备必需输入此的 PIN 码。 | 更改燈光設備的驗證 PIN 碼，最多只能輸入八位數位。 連接到該設備必需輸入此的 PIN 碼。 | AquaPinyin.Pages/SettingPage.cs:79 |
| `SEARCH_AGAIN` | 82 | SEARCH AGAIN | 再次搜索 | 再次搜尋 | AquaPinyin.PageModels/HomePageModel.cs:218 |
| `WHITE_WARM` | 83 | Warm White | 暖白光 | 暖白光 | AquaPinyin.Pages/Lamp4CPage.cs:328 |
| `PP_TITLE` | 84 | Privacy prompt | 温馨提示 | 温馨提示 | AquaPinyin.Pages.Controls/PPDialog.cs:44 |
| `MODE_TIMING` | 85 | TIMING | 定时模式 | 定時模式 | AquaPinyin.PageModels/Lamp4CPageModel.cs:126 |
| `AUTH_FAILURE` | 86 | Failure to authenticate, device refuses to connect! | 验证失败，设备拒绝连接！ | 驗證失敗，設備拒絕連接！ | AquaPinyin.PageModels/DevicePageModel.cs:316 |
| `CONNECTING` | 87 | CONNECTING... | 正在连接... | 正在連接... | AquaPinyin.PageModels/DevicePageModel.cs:160 |
| `LOGIN_SET` | 88 | LOGIN SETTINGS | 验证设置 | 驗證設置 | AquaPinyin.Pages/SettingPage.cs:42 |
| `FAILURE_TO_CONNECT` | 89 | Failure to connect device!{0}({1}) | 连接设备失败！{0}({1}) | 連接設備失敗！ {0}({1}) | AquaPinyin.PageModels/DevicePageModel.cs:194 |
| `EDIT` | 90 | Edit | 自定义模式编辑 | 自訂模式編輯 | AquaPinyin.Pages/Lamp4CPage.cs:400 |
| `AUTH` | 91 | AUTHENTICATION | 验证 | 驗證 | AquaPinyin.PageModels/HomePageModel.cs:243 |
| `DEMO` | 92 | DEMO | 快速演示 | 快速演示 | AquaPinyin.Pages/Lamp4CPage.cs:371 |
| `DIY1` | 93 | DIY 1 | 自定义 1 | 自訂 1 | AquaPinyin.Pages/Lamp4CTimersPage.cs:586, AquaPinyin.Pages/Lamp4CPage.cs:362 |
| `DIY3` | 94 | DIY 3 | 自定义 3 | 自訂 3 | AquaPinyin.Pages/Lamp4CTimersPage.cs:588, AquaPinyin.Pages/Lamp4CPage.cs:368 |
| `DIY2` | 95 | DIY 2 | 自定义 2 | 自訂 2 | AquaPinyin.Pages/Lamp4CTimersPage.cs:587, AquaPinyin.Pages/Lamp4CPage.cs:365 |
| `BLUE` | 96 | Blue | 蓝色 | 藍色 | AquaPinyin.Pages/Lamp4CTimersPage.cs:770, AquaPinyin.Pages.Controls/Palette.cs:92 |
| `NAME` | 97 | NAME: | 名称： | 名稱： | AquaPinyin.Pages/SettingPage.cs:49 |
| `VIEW` | 98 | View | 预设模式查看 | 預設模式查看 | AquaPinyin.Pages/Lamp4CPage.cs:382 |
| `TIME` | 99 | Time | 时间 | 時間 | AquaPinyin.Pages/Lamp4CTimersPage.cs:758 |
| `STOP` | 100 | STOP | 停止 | 停止 | (unused) |
| `SAVE` | 101 | SAVE | 保存 | 保存 | AquaPinyin.PageModels/SettingPageModel.cs:138, AquaPinyin.Pages/SettingPage.cs:83 |
| `NO_DEVICES_FOUND` | 102 | No devices found. | 未找到设备。 | 未找到設備。 | AquaPinyin.PageModels/HomePageModel.cs:44, 219 |
| `TIME_LB` | 103 | Time: | 时间： | 時間： | AquaPinyin.Pages/Lamp4CTimerPage.cs:197 |
| `LIGHT_MODULES` | 104 | LIGHT MODULES | 灯光设备 | 燈光設備 | AquaPinyin/AppShell.cs:49 |

## 11. Text and images that are not in the resources

| Text | Where |
|---|---|
| "P-LIGHT CONTROL" | `AquaPinyin.Pages.Controls/FlyoutHeader.cs:46` |
| "MENU" (toolbar), "LIGHT MODULES" (page title) | `AquaPinyin.Pages/Lamp4CPage.cs:707`, `AquaPinyin.PageModels/HomePageModel.cs:189` |
| "Up to 12 timers can be set." | `AquaPinyin.Pages/Lamp4CTimersPage.cs:705` |
| "Permission required" / "Apps need to grant permission to use Bluetooth devices." / "Close" | `AquaPinyin.PageModels/HomePageModel.cs:337` |
| "The connection timedout!" | `AquaPinyin.PageModels/DevicePageModel.cs:168` |
| "Name is too long" | `AquaPinyin.Services/BaseDevice.cs:129` |
| "Brightness: OFF" | `AquaPinyin.Pages/Lamp4CPage.cs:237` |
| "Version: …", "Powered by .Net MAUI, CLR: …" | `AquaPinyin.Pages.Controls/FlyoutFooter.cs:27-28` |
| Guide text (English) | `AquaPinyin.Pages.Controls/Guide_1.cs:71-170` |

| Image | Depicts | Used at |
|---|---|---|
| `sc1.png` … `sc4.png` (+ `_p` selected) | sun behind cloud, cloud with bolt, sun, crescent moon (72 × 82 px, grey / white) | `AquaPinyin.Pages/Lamp4CPage.cs:1265-1351` |
| `ico_bulb_r`, `ico_bulb_f` | dim and bright bulb at the ends of the brightness slider | `AquaPinyin.Pages/Lamp4CPage.cs:807, 900` |
| `ico_power_on`, `ico_power_off` | round power button | `AquaPinyin.PageModels/DevicePageModel.cs:35, 244` |
| `ico_tab_basic`, `ico_tab_pro` | bottom tabs | `AquaPinyin.Pages/Lamp4CPage.cs:2232, 2279` |
| `ico_more` | toolbar MENU | `AquaPinyin.Pages/Lamp4CPage.cs:706` |
| `ico_ble`, `ico_guide` | flyout items; `ico_ble` also on the scan page | `AquaPinyin/AppShell.cs:97, 102`, `AquaPinyin.Pages/HomePage.cs:1126` |
| `dev_pylamp_4c` | icon of a found PY4C light | `AquaPinyin.PageModels/ScanDevice.cs:22` |
| `ico_tag`, `ico_sec` | name row and PIN row of the settings page | `AquaPinyin.Pages/SettingPage.cs:190, 360` |
| `ico_add`, `ico_timer_off` | add-point toolbar item; header of the point editor | `AquaPinyin.Pages/Lamp4CTimersPage.cs:850`, `AquaPinyin.Pages/Lamp4CTimerPage.cs:392` |
| `doc_btn1.png`, `doc_btn2.png` | the controller's "Hour +" and "Minute −" buttons | `AquaPinyin.Pages.Controls/Guide_1.cs:130-136` |
| `banner0.png` … `banner9.png`, `line_1.png` | marketing banner and separators of the scan page | `AquaPinyin.PageModels/HomePageModel.cs:275`, `AquaPinyin.Pages/HomePage.cs:478` |

Colours of the tiles: unselected `#fafaf9`, selected `#777e92` with text `#f3f3f0`
(`AquaPinyin.Pages.Controls/SelectButton.cs:8-12`, `AquaPinyin.Pages.Controls/SelectImageButton.cs:9-11`).

## 12. What the app can do that Home Assistant should mirror, and what it hides

Covered by the integration: power, brightness, RGB + white colour, the 8 scenarios, the 6 programs (select), DEMO, the DIY
editor with live preview (12 points, minute resolution, unique times), time sync, rename, PIN change, factory-reset hint.

Present in the code but never used by the app: `AdjSpeed` (and on the real light it does nothing), channel bytes 0-2 of the
white-channel frame (they set red/green/blue individually), brightness values 0 and inside programs, preview over any mode.
See PROTOCOL.md → *Hidden features*.
