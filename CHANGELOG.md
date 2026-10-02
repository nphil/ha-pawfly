# Changelog

## 0.2.0 - 2026-10-02

- Every restart or shutdown of Home Assistant now lets go of the light's Bluetooth link cleanly,
  on its own. The old hook ran at the same moment Bluetooth was shutting down and could lose that
  race, leaving a half-open link on the ESPHome proxy. It now runs in Home Assistant's earlier
  shutdown stage, one job per light, with an 8 second limit, and never blocks the shutdown.
- From that moment the integration never connects again in that Home Assistant run: no reconnects,
  polls, commands, setup or pending `release_link` resume. Outage timers and the "unreachable"
  repair are stilled first, so the deliberate disconnect is not reported as an outage.
- `release_link` and the Release link button are unchanged.

## 0.1.0 - 2026-09-30

First release.

- Persistent Bluetooth link through local adapters and ESPHome proxies (subscribe, key check,
  clock sync, status on every connect; bounded teardown; backoff 1/2/5/10/30/60 s; preferred-proxy
  routing), or on-demand connections with `keep_connected` off (never released in the middle of a
  command).
- Light entity with brightness, RGBW colour, the eight scenes and Demo as effects; `select` for
  manual/program mode, mode and connectivity diagnostics, release-link and (disabled) sync-time
  buttons.
- Actions: `get_program`, `set_program` (read-back verified, one retry), `set_channel`, `preview`
  (latest-wins, auto-end, an end request always ends or fails), `sync_time`, `rename`,
  `change_password` (proved on a fresh link; a new key the light could not be asked about in time stays
  pending and is settled on the next connect), `release_link` (bounded, honest about failures, resumes
  only the same enabled entry, the newest request wins).
- Config flow with Bluetooth discovery and live password check, reauth, options flow; repair when a
  light stays unreachable for 15 minutes (outage clock survives reloads); diagnostics with the
  password redacted.
- Light attributes for dashboard cards: `mode`, `program`, `program_name`, `scenario`, and the static
  `programs`, `scene_colors`, `max_program_points`. The Lovelace card is a separate HACS plugin.
- No effect-speed control: the firmware ignores that opcode (verified on hardware).
