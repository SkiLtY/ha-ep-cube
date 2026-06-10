## 🪜 EP Cube Integration v1.2.0 — Mislabel Fix + Cube-Native Rollups + Instant Tiles

> **TL;DR** — three additive threads in one release. (1) The cube's `selfHelpRate` field has been mislabeled as "Self-consumption" since v0.5; it actually reports **self-sufficiency**. Display name corrected, entity_id preserved for back-compat. (2) Eight new cube-native monthly + yearly rollups replace the HA-side `utility_meter` helpers that previous releases shipped in the example package — more accurate, no drift on month/year roll. (3) Three new instant-KPI sensors + dashboard gauges answer "what's the house doing right now?" without users reading the power-flow card.

### ✨ What's new

**15 new sensors**, all additive — no new cloud calls, all derive from existing polls.

#### Self-consumption / sufficiency surface — corrected + expanded

| Sensor | Unit | Source |
|---|---|---|
| `sensor.ep_cube_self_sufficiency_pct` | % | Cube's `selfHelpRate` (renamed from `self_consumption_pct`; `unique_id` preserved) |
| `sensor.ep_cube_self_consumption_today` | % | `(solar_today − grid_export_today) / solar_today × 100` |
| `sensor.ep_cube_self_consumption_yesterday` | % | Same against yesterday bucket |
| `sensor.ep_cube_self_sufficiency_today` | % | `(load_today − grid_import_today) / load_today × 100` |
| `sensor.ep_cube_self_sufficiency_yesterday` | % | Same against yesterday bucket |

Verified 2026-06-10 against the live cube: on a 0.23 kWh import + 14.36 kWh load + 11.93 kWh export + 29.95 kWh solar day, the existing sensor read **99%** (= 98.4% self-sufficiency, which it is) while true self-consumption was only **~60%** (11.93/29.95 exported). The new pair surfaces both numbers honestly.

#### Cube-native monthly + yearly rollups

| Sensor | Unit | Source |
|---|---|---|
| `sensor.ep_cube_grid_import_month` / `_year` | kWh | `queryDataElectricityV2` scope=2 / scope=3 |
| `sensor.ep_cube_grid_export_month` / `_year` | kWh | Same |
| `sensor.ep_cube_solar_month` / `_year` | kWh | Same |
| `sensor.ep_cube_backup_loads_month` / `_year` | kWh | Same |

All `state_class=TOTAL` (not `TOTAL_INCREASING`) because they reset at the month/year boundary — HA's statistics engine handles the snap-back as a normal delta rather than spurious counter resets. **More accurate than the HA-side `utility_meter` helpers** that previous releases shipped in the example package: cube-native rollups don't drift if HA is down at month/year roll, because the cube itself owns the boundary snap-back.

#### Instant-KPI tiles

| Sensor | Unit | Behaviour |
|---|---|---|
| `sensor.ep_cube_self_consumption_right_now` | % | `(solar_w − export_w) / solar_w × 100`, `unknown` below 50 W solar |
| `sensor.ep_cube_self_sufficiency_right_now` | % | `(load_w − import_w) / load_w × 100`, `unknown` below 50 W load |
| `sensor.ep_cube_grid_flow_right_now` | W | Signed; ±200 W dead-band rounded to 0 to stop the gauge twitching on routine in-house transients (kettle / microwave / fridge cycles) |

Dashboard grows a **"Right now"** horizontal-stack of three `type: gauge` cards above the power-flow card, with severity bands matching what the cube is doing (self-consumption / sufficiency green > 70%, yellow 30-70%, red < 30%; grid flow green when exporting, yellow on modest import, red on sustained import > 3 kW).

### 💥 What changes for existing users

**The self-consumption rename is the only user-visible change.** Existing installs:

- **Display name** changes from "Self-consumption" to "Self-sufficiency" automatically on update.
- **`entity_id` stays `sensor.ep_cube_self_consumption`** — `unique_id` is preserved across the rename, so Energy Dashboard wiring, automations, templates, and history all keep working unchanged.
- **Fresh installs** (post-v1.2) get `sensor.ep_cube_self_sufficiency` as the entity_id slug.

If you'd like to manually update the entity_id on an existing install: Settings → Devices & services → EP Cube → click the renamed sensor → settings (gear) → change the entity_id manually. Optional; the legacy slug stays sticky otherwise.

### 🔧 Behaviour changes

- **Dashboard refresh**: `dashboards/ep_cube.yaml` adds the three gauge cards in a "Right now" horizontal-stack at the top of the Overview view.
- **Example package cleanup**: `examples/ha_config/packages/ep_cube.yaml` drops the `utility_meter` rollups for solar / grid / backup / nonbackup (monthly + yearly). Use the new cube-native sensors directly. The battery charge/discharge `utility_meter` rollups stay — the cube doesn't expose signed battery flow on the stats endpoint, so the client-side delta-tracker + `utility_meter` chain is still the only path for monthly/yearly battery totals.
- **Translations**: `de` / `it` / `nl` previously followed the English mislabel ("Eigenverbrauchsquote" / "Autoconsumo" / "Zelfverbruik"). Corrected to "Autarkiegrad" / "Autosufficienza" / "Zelfvoorziening". Plus 14 new entries per locale for the v1.2 additions.

### 🧪 Tests

Suite grows from **178 → 216** cases (+38). All in `tests/test_derived_pct_sensors.py`:

- 19 cases × `_self_consumption_pct` + `_self_sufficiency_pct` (kWh-domain bucket value_fns): normal-case math, divisor below jitter threshold → None, clamp to [0,100], empty bucket / missing field / non-numeric handling, bucket-routing
- 19 cases × `_instant_self_consumption_pct` + `_instant_self_sufficiency_pct` + `_instant_grid_flow_w` (W-domain): pass-through, sub-50-W noise floor, ±200 W dead-band edge handling

All green on Python 3.12 (validate.yml).

### 📦 Upgrading

- **HACS users**: bump v1.1.3 → v1.2.0 in the HACS UI. Restart HA. The renamed sensor's display name updates immediately; entity_id stays put.
- **Manual users**: re-copy `custom_components/ep_cube/` from the release zip. Restart HA.
- **If you use the example package** (`examples/ha_config/packages/ep_cube.yaml`): the file in this release drops 8 `utility_meter` entries (solar/grid/backup × month/year). If you have customisations layered on top, merge them onto the v1.2 base.
- **If you use the dashboard YAML** (`dashboards/ep_cube.yaml`): the v1.2 file adds a "Right now" horizontal-stack of three gauges at the top. If you customised the dashboard, the gauges are pasteable as a single horizontal-stack block.

### 🛣 What's next

- **v1.3** — lifetime totals (Phase 4.2 Tier 3 — RestoreSensor + coordinator-startup state-seeding) + eco metrics (`coal` / `treeNum` from `queryDataElectricityV2`).
- **HACS Default merge** — [hacs/default#8364](https://github.com/hacs/default/pull/8364) is in queue (834 PRs deep at submission; expected wait weeks-to-months). All bot checks green.
- **v1.x** — [HomeAssistant-OctopusEnergy](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy) consumption swap, gated on Octopus Home Mini arrival (ETA 2026-06-17 → 2026-07-01).

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
