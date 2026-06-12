## ⚡ EP Cube Integration v1.3.0 — Dashboard Refresh + Signed Grid Net

> **TL;DR** — single-session UX overhaul. (1) Dashboard restructured into three tabs (Now / Today / Control) — the single Overview mixed instant gauges with daily kWh rollups and control widgets in one viewport. (2) Now tab swaps the SC/SS instant percentage gauges (which pinned at 100% most of the day because a 20 kWh battery buffers the difference) for live power gauges in W: Solar, Battery, Grid. (3) Two new derived sensors (`grid_net_today` + `grid_net_yesterday`) give a single signed-kWh view, mirroring the Now-tab Grid gauge's centered-needle convention. (4) SC + SS today/yesterday now return `0.0` instead of `unknown` when their divisor is below the jitter floor — fixes the "Entity is non-numeric" overlay that hit gauges every morning before sunrise.

### ✨ What's new

#### Signed grid net — two new sensors

| Sensor | State class | Source |
|---|---|---|
| `sensor.ep_cube_grid_net_today` | TOTAL (signed kWh) | `gridelectricityfrom − gridelectricityto` from today bucket |
| `sensor.ep_cube_grid_net_yesterday` | TOTAL (signed kWh) | Same against yesterday bucket |

Positive = net importer for the period; negative = net exporter. The existing monotonic `grid_import_*` / `grid_export_*` sensors stay (Energy Dashboard wiring needs `TOTAL_INCREASING`) — the new pair is purely for at-a-glance dashboard gauges. Translations shipped for en / de / it / nl.

#### Dashboard refresh — three tabs

The single Overview view mixed timescales (instant gauges next to daily kWh totals next to control widgets), making it hard to read at a glance. Restructured into three tabs:

**Now** — what's happening right this second
- Three live power gauges in W (Solar / Battery / Grid), refreshed on the 30s coordinator cadence with no dead-band wrapping — the jitter IS the live feedback
- Power-flow card
- Battery status

**Today** — daily rollups, mirroring HA's native Energy tab
- Three gauges across the top (Grid net / Self-consumption / Self-sufficiency), refreshed every 5 min from the stats coordinator
- Energy today + Energy yesterday entity lists
- Matching three-gauge row above Energy yesterday so the rollup view is symmetric

**Control** — operating mode + mode-specific settings (unchanged behaviour, just moved into its own tab)

#### Unified colour palette

Replaced the per-gauge ad-hoc colouring with a consistent palette across all signed gauges:

- **Solar** — Material Design Green (#43a047) — renewable generation
- **Battery** — purple (#8353d1) when discharging (< 0, energy out), blue (#488fc2) when charging (> 0, energy in)
- **Grid** — purple (#8353d1) when exporting (< 0, energy out), blue (#488fc2) when importing (> 0, energy in)
- **Percentage gauges** (SC / SS today and yesterday) — Material green throughout, no severity bands

Purple/blue carries the same "energy going out" / "energy coming in" meaning on both Battery and Grid gauges, so a glance tells you which way energy flows regardless of which gauge you're reading. Matches HA's Energy Dashboard convention (purple = returned, blue = consumed). The previous 7-stop and 14-stop gradient experiments were dropped — interesting visually but added no signal users couldn't read from the numeric value.

### 🔧 Behaviour changes

- **SC + SS today/yesterday** now return `0.0` instead of `unknown` when their divisor (solar / load in kWh) is below the jitter floor (~0.05 kWh). HA's gauge card surfaces `unknown` as an "Entity is non-numeric" error overlay, which made those gauges error-out every morning before sunrise. Genuine missing-data states (no stats fetched yet / bucket keys absent / malformed values) still return `unknown` — that signal is preserved for when something's actually wrong upstream.
- **Energy today entity list** was pointing at the back-compat-preserved `sensor.ep_cube_self_consumption`, which reads 0 on cube firmware `02200242022220260515+` (the cube stopped exposing `selfHelpRate` on `homeDeviceInfo` in that build). Swapped to the new derived `sensor.ep_cube_self_consumption_today` and added a `Self-sufficiency` row.
- **Energy yesterday entity list** gains the matching `Self-consumption` and `Self-sufficiency` rows it was missing.

### 🧪 Tests

Suite grows from **216 → 223** cases (+7). All in `tests/test_derived_pct_sensors.py`:

- 7 new cases × `_grid_net`: net-importer, net-exporter, balanced, empty/missing/non-numeric handling, bucket-routing
- 2 existing SC + SS jitter-floor tests updated to assert `== 0.0` instead of `is None`

All green on Python 3.12 (validate.yml).

### 📦 Upgrading

- **HACS users**: bump v1.2.0 → v1.3.0 in the HACS UI. Restart HA.
- **Manual users**: re-copy `custom_components/ep_cube/` from the release zip. Restart HA.
- **If you use the dashboard YAML** (`dashboards/ep_cube.yaml`): the layout has shifted from a single Overview view to three tabs. Easiest path is to repaste the file via Raw configuration editor. If you customised the dashboard, the three new view blocks (`- title: Now` / `- title: Today` / `- title: Control`) are pasteable independently.

### 🛣 What's next

- **v1.4** — lifetime totals (Phase 4.2 Tier 3 — RestoreSensor + coordinator-startup state-seeding) + eco metrics (`coal` / `treeNum` from `queryDataElectricityV2`). Originally pencilled for v1.3 but today's UX work filled the slot; the roadmap items roll forward unchanged.
- **HACS Default merge** — [hacs/default#8364](https://github.com/hacs/default/pull/8364) is in queue (834 PRs deep at submission; expected wait weeks-to-months). All bot checks green.
- **v1.x** — [HomeAssistant-OctopusEnergy](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy) consumption swap, gated on Octopus Home Mini arrival (ETA 2026-06-17 → 2026-07-01).

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
