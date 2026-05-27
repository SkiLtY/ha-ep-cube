# ha-ep-cube

Home Assistant custom integration for the **Canadian Solar EP Cube** residential battery, with a Predbat-compatible service layer for Octopus Agile tariff optimisation.

> **Status:** pre-alpha. Integration is live against the real `monitoring-eu.epcube.com` cloud — all 9 sensors populating with verified values, Predbat plans against live Octopus Agile rates + Solcast PV forecasts and fires shim services that translate to `switchMode` writes. Phase 3.2 fully landed 2026-05-21: one-time email + password replaces the JSESSIONID-paste-every-hour UX, captcha-solving in pure numpy, silent re-auth on token expiry, mock-server realigned to the mobile-app surface, wire-level gotchas documented in TROUBLESHOOTING. APIs and entity shapes will still change before HACS distribution (Phase 4).

## Supported regions

The EP Cube cloud runs region-specific hosts. The config flow exposes a region picker; pick the one that matches the cloud your mobile app talks to.

| Region | Host | Status |
|---|---|---|
| **EU** | `monitoring-eu.epcube.com` | ✅ Verified live (UK + most of Europe — installer's default for DE / IT / NL / FR / ES / etc.) |
| **US** | `epcube-monitoring.com` (path prefix `/app-api`) | 🧪 **Experimental.** Host + path derived from public sources; not yet live-tested. Please open an issue with the result if you try it. |
| **JP** | `monitoring-jp.epcube.com` | Untested. |
| **Other** | user-supplied | Escape hatch for Australia, Canada, custom mocks, or any market where the host above is wrong. Capture the host from your EP Cube mobile app's network traffic and paste it in. |

## Why

The EP Cube has no documented local API (no Modbus, no MQTT). All control today goes through Canadian Solar's cloud via mobile-app endpoints. There is one existing community integration ([Bobsilvio/epcube](https://github.com/Bobsilvio/epcube)) — but no licence file, so we cannot legally fork it. This is a clean-room build.

The end goal: working Octopus Agile control via [Predbat](https://github.com/springfall2008/batpred). Predbat expects a rate-based, time-windowed control contract; EP Cube exposes a mode + TOU-schedule contract. The integration includes a shim service layer that translates between the two.

## Layout

```
ha-ep-cube/
├── custom_components/ep_cube/   ← The HA integration (HACS-installable later)
│   ├── services.py              ← Predbat shim service handlers (Phase 2b.1 contract)
│   └── predbat_state.py         ← Reads predbat.best_charge_* / best_export_* entities
├── mock_server/                 ← FastAPI mock of the EP Cube cloud, for dev without hardware
├── dashboards/
│   └── ep_cube.yaml             ← Lovelace dashboard (animated power flow + mode-aware controls)
├── docs/
│   ├── ARCHITECTURE.md          ← Predbat shim contract + design notes
│   ├── PREDBAT.md               ← Predbat install + tariff (BottlecapDave auto-detect) + Solcast runbook
│   ├── PHASE_3_2.md             ← Phase 3.2 bearer-token + captcha refactor notes
│   ├── MITMPROXY_SETUP.md       ← Cloud-API capture tooling (for contributors)
│   ├── TROUBLESHOOTING.md       ← Known gotchas
│   └── predbat_apps.yaml.example  ← Predbat custom-inverter template
└── docker-compose.yml           ← HA + mock-server stack
```

## Dev setup

Requires Docker. Bring up the stack:

```bash
docker compose up -d
```

This runs:
- **Home Assistant** on http://localhost:8123 with `custom_components/ep_cube/` volume-mounted
- **Mock EP Cube cloud** on http://localhost:8765

Add the integration through HA's UI; point it at `http://mock:8765` (Docker network DNS), any username/password, device_id `ep_cube_test_01`.

## Roadmap

| Phase | Status | What |
|---|---|---|
| 1 | ✅ | Mock cloud + HA integration skeleton + 9 sensors + DeviceInfo |
| 2a | ✅ | Predbat shim: 7 services, baseline snapshot, idempotency, auto-revert |
| 2b | ✅ | Predbat as `nipar44/predbat_addon` container, plan loop validated end-to-end |
| 2b.1 | ✅ | Shim reads params from `predbat.best_charge_*` / `predbat.best_export_*` entities |
| 2c | ✅ | Live Octopus Agile rates via public REST API (no Octopus account required) |
| 2c+ | ✅ | Solcast PV forecast wired in (split E/W array) |
| 3 | ✅ | Hardware reconciliation — live cloud bring-up against `monitoring-eu.epcube.com` |
| 3.1 | ✅ | `charge_freeze` → mid-peak TOU slot; force-export gap documented; stable device name |
| 3.2 | ✅ | Mobile-app Bearer-token auth replaces JSESSIONID-cookie paste |
| 3.3 | ✅ | Animated power-flow Lovelace dashboard (`dashboards/ep_cube.yaml`) |
| 3.4 | ✅ | Feature-parity catch-up vs Bobsilvio/epcube — control entities + daily kWh sensors + i18n |
| 3.5 | ✅ | Bobsilvio-parity metrics expansion — 5 sensors + 4 utility_meter rollups |
| 4 | 🚧 | HACS distribution (scaffolding live, brand assets self-hosted at `custom_components/ep_cube/brand/`). Remaining: relocate `ha_config/` helpers to `examples/`, tests, first `v0.1.0` tag |
| 4.1 | ⏸️ | TOU schedule editor — `set_tou_schedule` service + Lovelace editor card (post-HACS) |
| 4.2 | ⏸️ | Cloud-stats endpoint expansion — capture `queryDataElectricityV2` for signed grid import/export, `*_yesterday` variants, lifetime totals |
| 4+ | ⏸️ | BottlecapDave [HomeAssistant-OctopusEnergy](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy) — half-hourly smart-meter consumption replaces Riemann `load_today`. Gated on Octopus Home Mini arrival |

## Services exposed

### Predbat shim (`services.py`)

All shim services accept only an optional `device_id`. Window/SoC parameters are read from the entities Predbat publishes (`predbat.best_charge_*` / `predbat.best_export_*`) — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract.

| Service | Purpose |
|---|---|
| `ep_cube.charge_start` | Force grid charge until end of Predbat's planned charge window, target SoC from `best_charge_limit` |
| `ep_cube.charge_stop` | Cancel active charge override, restore baseline |
| `ep_cube.discharge_start` | Force discharge until end of planned export window, target SoC from `best_export_limit` |
| `ep_cube.discharge_stop` | Cancel active discharge override |
| `ep_cube.charge_freeze` | Hold battery at current SoC until end of charge window |
| `ep_cube.discharge_freeze` | Alias for charge_freeze, end-time taken from export window |
| `ep_cube.idle` | Restore baseline TOU schedule |

### Native (1:1 with cloud API)

| Service | Purpose |
|---|---|
| `ep_cube.set_tou_schedule` | Replace the full TOU schedule |

Mode switching + reserve-SoC writes are exposed as entities, not services:
`select.ep_cube_operating_mode`, `switch.ep_cube_allow_grid_charge`,
`number.ep_cube_self_consumption_reserve`, `number.ep_cube_backup_reserve`.
Call `select.select_option` / `number.set_value` from automations.

### Sensors (read)

`battery_soc`, `battery_soc_kwh` (energy), `battery_capacity_kwh`, `battery_power`, `grid_power`, `solar_power`, `load_power`, `operating_mode`, `reserve_soc`. All grouped under one device per EP Cube.

## Dashboard

[`dashboards/ep_cube.yaml`](dashboards/ep_cube.yaml) is a drop-in Lovelace dashboard that mirrors the EP Cube mobile app: live animated power flow at the top, battery status, an operating-mode picker, and mode-specific control cards (self-consumption / backup / time-of-use) that swap automatically when you change mode.

**Install:**

1. **Install the power-flow card via HACS** — HACS → Frontend → search "Power Flow Card Plus" by [flixlix](https://github.com/flixlix/power-flow-card-plus) → Install. Restart HA so the resource registers.
2. **Add a new dashboard** — Settings → Dashboards → Add Dashboard → "New dashboard from scratch" (title: *EP Cube*, icon: `mdi:home-battery`).
3. **Paste the YAML** — open the new dashboard → Edit → three-dot menu → *Raw configuration editor* → replace the contents with [`dashboards/ep_cube.yaml`](dashboards/ep_cube.yaml) → Save.

Entity IDs in the YAML assume the integration's default device name (`EP Cube`). If you renamed the device, or have multiple cubes, edit the YAML to match (HA auto-appends `_2`, `_3`, ... to disambiguate).

The TOU card has a slot reserved for a per-tier schedule editor — that ships with Phase 4.1 (`set_tou_schedule` service + Lovelace editor card, post-HACS).

## Energy dashboard

The integration ships four daily kWh sensors that mirror the cube's onboard counters (`sensor.ep_cube_solar_today`, `..._grid_today`, `..._backup_today`, `..._nonbackup_today`) plus a self-consumption KPI (`sensor.ep_cube_self_consumption`). Monthly + yearly rollups are layered on these via `utility_meter` in [`ha_config/packages/ep_cube.yaml`](ha_config/packages/ep_cube.yaml) (entity IDs `sensor.ep_cube_{solar,grid,backup,nonbackup}_{month,year}`).

To wire them into HA's built-in **Energy dashboard** (Settings → Dashboards → Energy):

- **Solar production** → `sensor.ep_cube_solar_today`
- **Grid consumption** → `sensor.ep_cube_import_today` (Riemann-integrated, monotonic import; resets daily)
- **Return to grid** → `sensor.ep_cube_export_today` (Riemann-integrated, monotonic export; resets daily)
- **Home battery** → use `sensor.ep_cube_battery_soc_kwh` for state-of-charge

`sensor.ep_cube_grid_today` (the cube-native counter) reports total grid throughput as a single direction-ambiguous magnitude — on a pure-export day it equals export, on a pure-import day it equals import, on mixed days direction is hidden. Verified 2026-05-24 against log math. Useful as a "matches what the EP Cube app shows" reference, but NOT what you want in the Energy Dashboard — the Riemann sensors above have clean signed direction.

## HA install type

This stack uses **HA Container** (lightweight, no Supervisor). HA Container can't install add-ons — Predbat runs as a sibling **`nipar44/predbat_addon` Docker container** (the upstream-recommended replacement for the now-deprecated AppDaemon install path). See [docs/PREDBAT.md](docs/PREDBAT.md).

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Canadian Solar or EP Cube.
