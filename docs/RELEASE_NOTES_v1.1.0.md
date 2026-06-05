## 🪜 EP Cube Integration v1.1.0 — Stats Endpoint + Clean Grid Import/Export Split

> **TL;DR** — 6 new sensors backed by the cube's own electricity-rollup endpoint (`queryDataElectricityV2`). Grid energy today is now correctly split into **import** + **export** rather than the single direction-ambiguous counter v1.0 inherited from v0.5.0. Yesterday's energy quartet is genuinely new surface for daily-summary automations.

### ✨ What's new

Six new sensors fed by a second background coordinator polling the cube's stats endpoint every 5 min for today + at slower cadences for the wider rollups. ≈320 cloud calls/day total, ~5× the existing `homeDeviceInfo` budget.

| Sensor | Unit | State class | Source |
|---|---|---|---|
| `sensor.ep_cube_grid_import_today` | kWh | `TOTAL_INCREASING` | `gridElectricityFrom` (scope=1 today) |
| `sensor.ep_cube_grid_export_today` | kWh | `TOTAL_INCREASING` | `gridElectricityTo` (scope=1 today) |
| `sensor.ep_cube_grid_import_yesterday` | kWh | `TOTAL` | `gridElectricityFrom` (scope=1 yesterday) |
| `sensor.ep_cube_grid_export_yesterday` | kWh | `TOTAL` | `gridElectricityTo` (scope=1 yesterday) |
| `sensor.ep_cube_solar_yesterday` | kWh | `TOTAL` | `solarElectricity` (scope=1 yesterday) |
| `sensor.ep_cube_backup_yesterday` | kWh | `TOTAL` | `backUpElectricity` (scope=1 yesterday) |

Today's pair (`grid_import_today` / `grid_export_today`) is the direct fix for the v0.5.0 limitation that v1.0 inherited: the cube's `homeDeviceInfo` field `gridElectricity` is direction-ambiguous — equals export on export-heavy days, import on import-heavy days. The new pair reads from the stats endpoint which exposes both directions cleanly, so HA's Energy Dashboard wires up correctly without Riemann tricks.

The yesterday quartet is genuinely new — no previous integration version surfaced it. Useful for "did I export much yesterday?" automations and for Energy Dashboard back-fill semantics.

### 💥 Breaking changes

- **Removed `sensor.ep_cube_grid_today`** — the direction-ambiguous counter. Replaced by `grid_import_today` + `grid_export_today`. **If you reference `grid_today` in automations / templates / dashboards, update them** to the new pair.
- **Removed `sensor.ep_cube_nonbackup_today`** — reported 0 across every window on EU firmware. Misleading. If you have a non-backed-loads-aware setup outside the EU, please open an issue — happy to restore as a region-gated sensor.

### 🔧 Behaviour changes

- **Dashboard refresh**: `dashboards/ep_cube.yaml` now has **Energy today** + **Energy yesterday** entities cards between the Battery card and the Operating-mode picker. Drop-in users get the new sensors surfaced without further wiring.
- **Stats coordinator** runs alongside the existing 60 s `homeDeviceInfo` coordinator. Today's bucket polls every 5 min; month every 1 h; year every 6 h; lifetime totals every 12 h; yesterday re-fetches only on HA-local date roll + once at startup. Best-effort: if a wider bucket fetch fails the previous cached value stays put and the integration doesn't flag.

### 🧪 Tests

Suite grows from 160 → 178 cases. 8 new in `tests/test_api_client.py::TestGetStats` covering the helper (URL composition per scope, lowercase normalisation, 403 reauth retry path, US `/app-api` prefix). 11 new in `tests/test_stats_coordinator.py` covering the cadence logic (per-bucket refresh thresholds, day-roll yesterday refetch, today-failure-raises-vs-non-today-best-effort error handling). All green on Python 3.12.

### 📦 Upgrading

- **HACS users**: bump v1.0.0 → v1.1.0 in the HACS UI. **Restart HA.** Update any automation / template / dashboard reference to `sensor.ep_cube_grid_today` to use the new import/export pair.
- **Manual users**: re-copy `custom_components/ep_cube/` from the release zip. Restart HA.

### 🛣 What's next

- **v1.2** — cube-native monthly + annual rollups (Phase 4.2 Tier 3). Will replace the `utility_meter` helpers in `examples/ha_config/packages/`.
- **v1.3** — lifetime totals (Phase 4.2 Tier 4) + eco metrics (Phase 4.2 Tier 5).
- **HACS Default submission** — held until v1.1 has bedded in.
- **v1.x** — [HomeAssistant-OctopusEnergy](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy) — half-hourly smart-meter consumption replaces Riemann `load_today`. Gated on Octopus Home Mini arrival.

### ☕ Support

If this saves you some hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
