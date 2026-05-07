# ha-ep-cube

Home Assistant custom integration for the **Canadian Solar EP Cube** residential battery, with a Predbat-compatible service layer for Octopus Agile tariff optimisation.

> **Status:** pre-alpha. Hardware arrives ~late May 2026. Until then, development is against a mock cloud server. APIs and entity shapes will change.

## Why

The EP Cube has no documented local API (no Modbus, no MQTT). All control today goes through Canadian Solar's cloud via mobile-app endpoints. There is one existing community integration ([Bobsilvio/epcube](https://github.com/Bobsilvio/epcube)) — but no licence file, so we cannot legally fork it. This is a clean-room build.

The end goal: working Octopus Agile control via [Predbat](https://github.com/springfall2008/batpred). Predbat expects a rate-based, time-windowed control contract; EP Cube exposes a mode + TOU-schedule contract. The integration includes a shim service layer that translates between the two.

## Layout

```
ha-ep-cube/
├── CLAUDE.md                    ← Primary context for AI-assisted dev
├── custom_components/ep_cube/   ← The HA integration (HACS-installable later)
├── mock_server/                 ← FastAPI mock of the EP Cube cloud, for dev without hardware
├── docs/
│   ├── ARCHITECTURE.md          ← Predbat shim contract + design notes
│   ├── <private-docs>       ← Bring-up steps for <host> (deploy key, clone, stack, pull)
│   ├── PREDBAT.md               ← Phase 2b — Predbat Docker container install
│   └── predbat_apps.yaml.example  ← Predbat custom-inverter template
├── docker-compose.yml           ← HA + mock-server stack for local iteration
└── ha_config/                   ← HA config volume (gitignored apart from .gitkeep)
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
- [x] **Phase 2a** — Predbat shim: 7 services, baseline snapshot, idempotency, auto-revert timer *(verified end-to-end on <host>, 2026-05-04)*
- [x] **Phase 2b** — Predbat running as `nipar44/predbat_addon` Docker container, plan loop validated against hardcoded test prices, fires shim service calls *(verified on <host>, 2026-05-05; see [docs/PREDBAT.md](docs/PREDBAT.md))*
- [ ] **Phase 2b.1** — Refactor shim to Predbat's actual service contract (params come from `sensor.predbat_<inv>_*` entities, not from service-call args)
- [ ] **Phase 2c** — Switch Predbat's price source to real Octopus Agile via [BottlecapDave's HACS integration](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy) *(blocked on Octopus account switch)*
- [ ] **Phase 3** — Hardware reconciliation: capture real cloud API traffic via mitmproxy, fix mock contract, switch to live endpoint *(blocked on hardware arrival)*
- [ ] **Phase 4** — HACS distribution, light test scaffolding, GitHub Actions

## Services exposed

### Predbat shim (`services.py`)

| Service | Purpose |
|---|---|
| `ep_cube.charge_start` | Force grid charge until `end_time`, target SoC |
| `ep_cube.charge_stop` | Cancel active charge override, restore baseline |
| `ep_cube.discharge_start` | Force discharge until `end_time`, target SoC |
| `ep_cube.discharge_stop` | Cancel active discharge override |
| `ep_cube.charge_freeze` | Hold battery at current SoC until `end_time` |
| `ep_cube.discharge_freeze` | Alias for charge_freeze |
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
