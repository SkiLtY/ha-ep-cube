# ha-ep-cube

Home Assistant custom integration for the **Canadian Solar EP Cube** residential battery, with a Predbat-compatible service layer for Octopus Agile tariff optimisation.

> **Status:** pre-alpha. Integration is live against the real `monitoring-eu.epcube.com` cloud — all 9 sensors populating with verified values, Predbat plans against live Octopus Agile rates + Solcast PV forecasts and fires shim services that translate to `switchMode` writes. Mock no longer regressions until step 6 of Phase 3.2 lands (now speaks the legacy web-portal surface; integration speaks the mobile `/api/device/*` surface). Phase 3.2 landed the mobile-app Bearer-token auth in steps 1–5 2026-05-21: one-time email + password replaces the JSESSIONID-paste-every-hour UX, captcha-solving in pure numpy, silent re-auth on token expiry. APIs and entity shapes will still change before HACS distribution (Phase 4).

## Why

The EP Cube has no documented local API (no Modbus, no MQTT). All control today goes through Canadian Solar's cloud via mobile-app endpoints. There is one existing community integration ([Bobsilvio/epcube](https://github.com/Bobsilvio/epcube)) — but no licence file, so we cannot legally fork it. This is a clean-room build.

The end goal: working Octopus Agile control via [Predbat](https://github.com/springfall2008/batpred). Predbat expects a rate-based, time-windowed control contract; EP Cube exposes a mode + TOU-schedule contract. The integration includes a shim service layer that translates between the two.

## Layout

```
ha-ep-cube/
├── CLAUDE.md                    ← Primary context for AI-assisted dev
├── custom_components/ep_cube/   ← The HA integration (HACS-installable later)
│   ├── services.py              ← Predbat shim service handlers (Phase 2b.1 contract)
│   └── predbat_state.py         ← Reads sensor.predbat_<inv>_* entities Predbat publishes
├── mock_server/                 ← FastAPI mock of the EP Cube cloud, for dev without hardware
├── ha_config/
│   ├── configuration.yaml       ← HA core config (loads packages/)
│   └── packages/ep_cube.yaml    ← Riemann load_today + input_numbers Predbat writes to
├── predbat_config/apps.yaml     ← Predbat config (BottlecapDave tariff auto-detect, Solcast auto-discovery, shim service map)
├── docs/
│   ├── ARCHITECTURE.md          ← Predbat shim contract + design notes
│   ├── <private-docs>       ← Bring-up steps for <host> (deploy key, clone, stack, pull)
│   ├── PREDBAT.md               ← Predbat install + tariff (BottlecapDave auto-detect) + Solcast runbook
│   ├── <private-runbook>               ← Install-day runbook (mitmproxy, capture order, mock → real swap)
│   ├── TROUBLESHOOTING.md       ← Known gotchas
│   └── predbat_apps.yaml.example  ← Predbat custom-inverter template
└── docker-compose.yml           ← HA + mock-server + predbat stack
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

Production deployment to Synology: see [<private-docs>](<private-docs>).

## Roadmap

- [x] **Phase 1** — Mock cloud + HA integration skeleton + 9 sensors + DeviceInfo *(verified on <host>, 2026-05-03)*
- [x] **Phase 2a** — Predbat shim: 7 services, baseline snapshot, idempotency, auto-revert timer *(verified, 2026-05-04)*
- [x] **Phase 2b** — Predbat running as `nipar44/predbat_addon` Docker container, plan loop validated, fires shim service calls *(verified, 2026-05-05)*
- [x] **Phase 2b.1** — Shim consumes the dummy entities Predbat publishes (`sensor.predbat_<inv>_*`) instead of expecting parameters in service-call args. New `predbat_state.py` is the single Predbat-aware module *(verified, 2026-05-07)*
- [x] **Phase 2c** — Live Octopus Agile rates via the public REST API (region L), no Octopus account required *(verified, 2026-05-07)*
- [x] **Phase 2c+** — Solcast PV forecast wired in (split E/W array). Predbat plan now schedules charges around forecast solar generation *(verified, 2026-05-07)*
- [x] **Phase 3** — Hardware reconciliation: capture real cloud API contract, rewrite mock + integration, switch to live endpoint *(all complete 2026-05-20 — two HAR captures, full mock + integration rewrite, JSESSIONID paste config-flow, first real-cloud poll green with all 9 sensors populating; capacity-from-`batteryPackNum` fix in [`c50e65a`](https://github.com/SkiLtY/ha-ep-cube/commit/c50e65a) handled a parallel-pack edge case the captures had missed)*
- [x] **Phase 3.1** — Cleanup: `charge_freeze` now uses a mid-peak TOU slot (genuinely idle per vendor docs); force-export gap documented as a known limitation (no native cloud equivalent — `discharge_start → peak` drains to loads + refuses imports, surplus exports only if `sellingEnable` permits); stable `EP Cube` device name so entity IDs are `sensor.ep_cube_<key>` regardless of devId *(landed 2026-05-21, commits [`61ab1bb`](https://github.com/SkiLtY/ha-ep-cube/commit/61ab1bb) + [`d4aae05`](https://github.com/SkiLtY/ha-ep-cube/commit/d4aae05))*
- [x] **Phase 3.2** — Mobile-app Bearer-token auth replaces JSESSIONID-cookie paste *(steps 1–5 landed 2026-05-21 — config flow now asks for the user's EP Cube account email + password, runs the 4-POST captcha login flow on submit, and silently re-logs-in on token expiry; full discovery notes + implementation links in [docs/PHASE_3_2.md](docs/PHASE_3_2.md))*. Mock-server update + TROUBLESHOOTING.md write-up still outstanding before Phase 4 HACS.
- [ ] **Phase 4** — HACS distribution, light test scaffolding, GitHub Actions
- [ ] **Phase 4+** — BottlecapDave's [HomeAssistant-OctopusEnergy](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy) integration. Strict upgrade — the public Agile URL keeps working either way. Adds:
  - Real half-hourly **smart-meter consumption** sensors (replaces the Riemann-integral `sensor.ep_cube_load_today` we ship in `ha_config/packages/ep_cube.yaml`; gives Predbat a real 7-day rolling load forecast instead of single-day accumulation)
  - **IOG dispatch slots** (only relevant if moving to a smart-EV tariff later)
  - Full account/tariff metadata for audit + reporting
  
  *Gating: wait for Octopus Home Mini (requested 2026-05-20, ETA 2026-06-17 to 2026-07-01) so we configure the integration once with real-time consumption rather than twice — half-hourly first, then real-time on OHM arrival. The Riemann-integral `load_today` continues to work acceptably in the interim.*

## Services exposed

### Predbat shim (`services.py`)

All shim services accept only an optional `device_id`. Window/SoC/rate parameters are read from the dummy entities Predbat publishes (`sensor.predbat_<inv>_*`) — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract.

| Service | Purpose |
|---|---|
| `ep_cube.charge_start` | Force grid charge until end of Predbat's planned charge window, target SoC from `charge_limit` |
| `ep_cube.charge_stop` | Cancel active charge override, restore baseline |
| `ep_cube.discharge_start` | Force discharge until end of planned discharge window, target SoC from `discharge_target_soc` |
| `ep_cube.discharge_stop` | Cancel active discharge override |
| `ep_cube.charge_freeze` | Hold battery at current SoC until end of charge window |
| `ep_cube.discharge_freeze` | Alias for charge_freeze, end-time taken from discharge window |
| `ep_cube.idle` | Restore baseline TOU schedule |

### Native (1:1 with cloud API)

| Service | Purpose |
|---|---|
| `ep_cube.set_operating_mode` | Switch self-consumption / time-of-use / backup |
| `ep_cube.set_tou_schedule` | Replace the full TOU schedule |

### Sensors (read)

`battery_soc`, `battery_soc_kwh` (energy), `battery_capacity_kwh`, `battery_power`, `grid_power`, `solar_power`, `load_power`, `operating_mode`, `reserve_soc`. All grouped under one device per EP Cube.

## HA install type

This stack uses **HA Container** (lightweight, no Supervisor). HA Container can't install add-ons — Predbat runs as a sibling **`nipar44/predbat_addon` Docker container** (the upstream-recommended replacement for the now-deprecated AppDaemon install path). See [docs/PREDBAT.md](docs/PREDBAT.md).

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Canadian Solar or EP Cube.
