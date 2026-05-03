# ha-ep-cube

Home Assistant custom integration for the **Canadian Solar EP Cube** residential battery, with a Predbat-compatible control surface for Octopus Agile tariff optimisation.

> **Status:** pre-alpha. Hardware arrives ~late May 2026. Until then, development is against a mock cloud server. APIs and entity shapes will change.

## Why

The EP Cube has no documented local API (no Modbus, no MQTT). All control today goes through Canadian Solar's cloud via mobile-app endpoints. There is one existing community integration ([Bobsilvio/epcube](https://github.com/Bobsilvio/epcube)) — but no licence file, so we cannot legally fork it. This is a clean-room build.

The end goal: working Octopus Agile control via [Predbat](https://github.com/springfall2008/batpred). Predbat expects a rate-based, time-windowed control contract; EP Cube exposes a mode + TOU-schedule contract. The integration includes a shim service layer that translates between the two.

## Layout

```
ha-ep-cube/
├── custom_components/ep_cube/   ← The HA integration (HACS-installable)
├── mock_server/                 ← FastAPI mock of the EP Cube cloud, for dev without hardware
├── docs/
│   └── ARCHITECTURE.md          ← Predbat shim contract + design notes
├── docker-compose.yml           ← HA + mock-server stack for local iteration
└── ha_config/                   ← HA config volume (gitignored)
```

## Dev setup

Requires Docker. Copy this repo and bring up the stack:

```bash
docker compose up -d
```

This runs:
- Home Assistant on http://localhost:8123 with `custom_components/ep_cube/` volume-mounted
- Mock EP Cube cloud on http://localhost:8765

Add the integration through HA's UI; point it at `http://mock:8765` (Docker network DNS).

## Roadmap

- [ ] Mock cloud server with auth + poll + setOperatingMode + setTouSchedule
- [ ] HA integration: config flow, coordinator, sensor entities
- [ ] HA services: set_operating_mode, set_tou_schedule
- [ ] Predbat shim services: charge_start / charge_stop / discharge_start / discharge_stop / freeze
- [ ] Predbat `apps.yaml` template
- [ ] Reconcile mock against real cloud API (post-hardware)
- [ ] HACS distribution

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Canadian Solar or EP Cube.
