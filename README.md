<div align="center">

# ☀️ EP Cube Integration ☀️

**Home Assistant integration for the Canadian Solar EP Cube residential battery**
*with a Predbat-compatible shim for Octopus Agile tariff optimisation*

[![HA Version](https://img.shields.io/badge/Home%20Assistant-2026.3%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Build](https://img.shields.io/github/actions/workflow/status/SkiLtY/ha-ep-cube/validate.yml?label=CI)](../../actions)
[![Release](https://img.shields.io/github/v/release/SkiLtY/ha-ep-cube?label=Release&color=41BDF5)](https://github.com/SkiLtY/ha-ep-cube/releases)
[![HACS](https://img.shields.io/badge/HACS-Default-41BDF5?logo=home-assistant&logoColor=white)](https://hacs.xyz)

</div>

---

> [!NOTE]
> **v1.2 renamed `sensor.ep_cube_self_consumption` → `sensor.ep_cube_self_sufficiency`** to fix a long-standing mislabel — the cube's `selfHelpRate` field reports self-sufficiency (% of load met from own generation), not self-consumption (% of generation used onsite). The underlying `unique_id` is preserved, so existing dashboards, automations and Energy Dashboard wiring keep working unchanged; only the display name updates. Fresh installs get the corrected `sensor.ep_cube_self_sufficiency` slug. v1.2 also adds 4 derived percentage sensors (today + yesterday × self-consumption / self-sufficiency) for symmetry with the Energy Dashboard's today / yesterday cards.

---

<div align="center">

![EP Cube dashboard — the Now tab: live headline, day plan, animated power flow and forecast chart](docs/dashboard.gif)

*A live drop-in Lovelace dashboard that mirrors the EP Cube mobile app — animated power flow, mode-aware controls, ships with the integration.*

</div>

---

## ✨ What You Get

- 🔋 **41 sensors + 5 control entities** (plus EV charger power + EV energy today when the cube reports an EV charger) surfacing every cube state — SoC, power flow, instant KPIs, mode, reserves, daily / yesterday / monthly / yearly energy, self-consumption + sufficiency %, lifetime stats
- ⚡ **Predbat shim** translates rate-based commands into the cube's TOU model — full **Octopus Agile** optimisation, no manual scheduling
- 🎨 **Drop-in animated dashboard** mirroring the EP Cube mobile app — power flow, mode picker, mode-specific control cards
- 🌍 **Multi-region** — EU live, US/JP/Other supported via config-flow region picker
- 🔐 **One-time email + password setup** with silent re-auth on token expiry — no JSESSIONID-paste UX, no recurring auth chores
- 🧪 **197-test pytest suite + CI** on every PR

---

## ⚡ Quick Start

**1. Install via HACS**

HACS → search for *Canadian Solar EP Cube* → Download → restart Home Assistant when prompted.

> [!NOTE]
> Newly added to the HACS Default store — it can take up to 8 hours from repository merge for a fresh listing to appear in the store search. If it's not there yet, add via HACS → ⋮ → *Custom repositories* → `https://github.com/SkiLtY/ha-ep-cube` as *Integration* as an interim path.

**2. Add the integration**

Settings → *Devices & services* → *Add integration* → search **Canadian Solar EP Cube** → enter your **region**, the **email + password** for the EP Cube mobile app, and submit.

The integration runs the captcha-solving login flow, fetches your device list, and registers 41 sensors + 5 control entities under one device.

<details>
<summary>Manual install (without HACS, or for pre-release testing)</summary>

```bash
cd /path/to/homeassistant/config
mkdir -p custom_components
git clone https://github.com/SkiLtY/ha-ep-cube /tmp/ha-ep-cube
cp -r /tmp/ha-ep-cube/custom_components/ep_cube custom_components/
```

Restart HA, then continue from step 2 above.

</details>

> [!TIP]
> **Want Predbat / Octopus Agile optimisation too?** After the integration is live, also:
> 1. Copy `examples/ha_config/` into your HA config dir (or merge into your existing `configuration.yaml`) — adds Riemann-integrated daily-kWh sensors for the HA Energy Dashboard.
> 2. Install Predbat via the [`nipar44/predbat_addon` Docker container](https://github.com/nipar44/predbat_addon). Full runbook in [docs/PREDBAT.md](docs/PREDBAT.md).
>
> **Want the animated power-flow dashboard?** See [Dashboard](#-dashboard) — one HACS frontend card + one paste of `dashboards/ep_cube.yaml`.

---

## 🌍 Supported Regions

| Region | Host | Status |
|--------|------|--------|
| **EU** | `monitoring-eu.epcube.com` | ✅ Verified live (UK, DE, IT, NL, FR, ES, …) |
| **US** | `epcube-monitoring.com` (path prefix `/app-api`) | 🧪 Experimental — host derived from public sources, not live-tested. Please open an issue with your result. |
| **JP** | `monitoring-jp.epcube.com` | ⬜ Untested |
| **Other** | User-supplied | Escape hatch for AU, CA, custom mocks, or any market where the above is wrong. Capture the host from your app's network traffic and paste it in. |

---

## 📡 Sensors

47 sensors, all grouped under one device per EP Cube:

| Group | Sensors |
|-------|---------|
| **Battery** | `battery_soc` · `battery_soc_kwh` · `battery_capacity_kwh` · `battery_power` · `battery_charge_today` · `battery_discharge_today` |
| **Power flow** | `grid_power` · `solar_power` · `load_power` |
| **Right now** | `instant_self_consumption_pct` · `instant_self_sufficiency_pct` · `instant_grid_flow_w` |
| **Daily energy (kWh)** | `solar_today` · `backup_today` · `grid_import_today` · `grid_export_today` · `solar_dc_today` · `solar_ac_today` |
| **Yesterday energy (kWh)** | `solar_yesterday` · `backup_yesterday` · `grid_import_yesterday` · `grid_export_yesterday` |
| **Monthly energy (kWh)** | `solar_month` · `backup_month` · `grid_import_month` · `grid_export_month` |
| **Yearly energy (kWh)** | `solar_year` · `backup_year` · `grid_import_year` · `grid_export_year` |
| **Efficiency (%)** | `self_sufficiency_pct` · `self_sufficiency_today` · `self_sufficiency_yesterday` · `self_consumption_today` · `self_consumption_yesterday` |
| **Mode + reserve** | `operating_mode` · `reserve_soc` · `dynamic_buy_below` · `dynamic_sell_above` (only while the cube is in Dynamic mode) |
| **Eco (the app's estimates)** | `coal_today` · `coal_year` · `trees_today` · `trees_year` |
| **Lifetime / KPI** | `earning_yesterday` · `grid_outage_count` · `off_grid_seconds` · `winter_protect` |

> [!TIP]
> **Monthly + yearly rollups are cube-native** — they read the cube's own boundary-aware accounting via `queryDataElectricityV2 scope=2/3`. Don't drift if HA is down at month/year roll, unlike HA-side `utility_meter` helpers (which previous releases shipped as a workaround). If you've been using the helpers from `examples/ha_config/packages/ep_cube.yaml`, you can delete the `ep_cube_{solar,grid,backup}_{month,year}` entries — the integration provides them directly now.

> [!NOTE]
> **`backup_today` / `backup_yesterday` are install-dependent.** They count kWh delivered through the cube's backup-output terminal — *not* "loads that stayed up during an outage". If your installer wired the whole house through the backup terminal (common in the UK with a critical-loads panel), these sensors read as whole-house consumption. If only essential circuits are wired through the backup output, you'll see just fridge / lighting / router etc. Outage-resilience under UK G99/G100 regs requires a separate **EPS Gateway**; without one the cube refuses to supply via the backup terminal during an actual grid outage even though it meters kWh through it under normal grid-up operation.

---

## ⚙️ Services

### Predbat Shim (`services.py`)

All shim services accept only an optional `device_id`. Window and SoC parameters are read from the entities Predbat publishes — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract.

| Service | Purpose |
|---------|---------|
| `ep_cube.charge_start` | Force grid charge until end of Predbat's planned charge window; target SoC from `best_charge_limit` |
| `ep_cube.charge_stop` | Cancel active charge override, restore baseline |
| `ep_cube.discharge_start` | Force discharge until end of planned export window; target SoC from `best_export_limit` |
| `ep_cube.discharge_stop` | Cancel active discharge override |
| `ep_cube.charge_freeze` | Hold battery at current SoC until end of charge window |
| `ep_cube.discharge_freeze` | Alias for `charge_freeze`; end-time taken from export window |
| `ep_cube.idle` | Restore baseline TOU schedule |

> [!IMPORTANT]
> **v1.0 is an opinionated Predbat bridge.** The integration treats Predbat as the single source of truth for time-of-use control. There is no user-facing TOU writer service. If the cube has user-painted TOU slots while Predbat is installed, an HA Repair issue offers a one-click wipe to hand control back to Predbat. Manual TOU painting belongs in the EP Cube mobile app or a different integration ([Bobsilvio/epcube](https://github.com/Bobsilvio/epcube) is a good fit for fixed-tariff non-Predbat users).

Mode switching and reserve-SoC writes are exposed as **entities**, not services:

```
select.ep_cube_operating_mode
switch.ep_cube_allow_grid_charge
number.ep_cube_self_consumption_reserve
number.ep_cube_backup_reserve
```

Call `select.select_option` / `number.set_value` from automations.

---

## 🧩 Helper Config (Optional)

The integration installs cleanly on a default HA setup. Two optional extras live in [`examples/ha_config/`](examples/ha_config/):

- **`packages/ep_cube.yaml`** — Riemann daily kWh sensors (load / PV / signed grid import / signed grid export), `utility_meter` monthly + yearly rollups, and `input_number` charge / discharge rate entities.
- **`configuration.yaml`** — minimal example showing the `homeassistant.packages` block.

| Use case | Need the package? |
|----------|:-----------------:|
| Running Predbat | ✅ Yes — `load_today` is a hard dependency; `charge_rate`/`discharge_rate` must be writable `input_number` entities |
| Monthly/yearly history in Energy dashboard | ✅ Recommended |
| Just install and view live sensors | ❌ No |

**To install:** copy `examples/ha_config/` into your HA config directory (merging into any existing `configuration.yaml`), then restart HA.

---

## 📊 Dashboard

[`dashboards/ep_cube.yaml`](dashboards/ep_cube.yaml) is a drop-in Lovelace dashboard mirroring the EP Cube mobile app, with these views:

| View | What it shows |
|------|---------------|
| **Now** | A "right now" headline (what the cube is doing, battery full by, next charge / export, live price), a plan strip for the day, the animated power flow, and a ±2 h power chart with Predbat's forecast dotted |
| **Today** | Power / Battery / Prices charts (tabs) plus energy-cost and energy cards |
| **Yesterday** | The same charts and cards for the previous day |
| **Week** | The last 7 days, plus week-to-date energy and cost cards (needs the weekly package below) |
| **Month** | Month-to-date energy and cost, with a chart of Octopus's billed bought / sold / net cost for each week of the calendar month (needs the same package; the net is shown with and without the standing charge) |
| **Control** | Operating-mode picker and mode-specific control cards that swap automatically when you change mode |

**Install steps:**

1. **Install these HACS frontend cards**, then reload your browser:
   - **Power Flow Card Plus** by [flixlix](https://github.com/flixlix/power-flow-card-plus)
   - **ApexCharts Card** by [RomRider](https://github.com/RomRider/apexcharts-card)
   - **Tabbed Card**
   - **Lovelace Card-mod** by [thomasloven](https://github.com/thomasloven/lovelace-card-mod)
   - **HTML Jinja2 Template card**
2. **New dashboard** — Settings → Dashboards → Add Dashboard → "New dashboard from scratch" (title: *EP Cube*, icon: `mdi:home-battery`).
3. **Paste YAML** — open dashboard → Edit → ⋮ → *Raw configuration editor* → replace contents with [`dashboards/ep_cube.yaml`](dashboards/ep_cube.yaml) → Save.
4. **Week-to-date cards (optional)** — copy [`examples/ha_config/packages/ep_cube_weekly.yaml`](examples/ha_config/packages/ep_cube_weekly.yaml) into your `/config/packages/` folder next to `ep_cube.yaml` and restart HA. The cards read zero until the weekly meters have data. The two billed-cost automations in that file mention your Octopus meter serial and MPAN, so replace the `<...>` parts with the ones in your own entity names first. If you copied an earlier version of this file, replace it with the new one: the old billed meters could only add increases, so they under-counted the week. The new file also adds the month totals the Month tab reads.
5. **EV bubble on the Energy Flow card (optional, only if your cube reports an EV charger)** — copy [`examples/ha_config/packages/ep_cube_ev.yaml`](examples/ha_config/packages/ep_cube_ev.yaml) next to the others, restart HA, then uncomment the marked lines in the Energy Flow card of `ep_cube.yaml`. Home then shows the house on its own, with the car as its own bubble.

> [!NOTE]
> The charts and cost tiles also assume the **Octopus Energy** integration (rate and billed-cost sensors) and **Predbat** (plan entities such as `predbat.soc_kw_best`). Entity IDs for your own Octopus meters are written into the YAML — search for `octopus_energy_electricity_` and replace them with yours. Without Predbat the forecast lines and plan strip stay empty.

> [!TIP]
> Entity IDs assume the default device name `EP Cube`. If you renamed the device, or have multiple cubes, edit the YAML accordingly — HA appends `_2`, `_3`, etc. to disambiguate.

---

## ⚡ Energy Dashboard

The integration ships cube-native daily kWh sensors that wire straight into HA's **Energy dashboard** (Settings → Dashboards → Energy) — no helper package needed.

| Slot | Entity |
|------|--------|
| Solar production | `sensor.ep_cube_solar_today` |
| Grid consumption | `sensor.ep_cube_grid_import_today` |
| Return to grid | `sensor.ep_cube_grid_export_today` |
| Home battery SoC | `sensor.ep_cube_battery_soc_kwh` |

> [!TIP]
> Prefer Riemann-integrated power-to-energy sensors instead? The optional [helper package](#-helper-config-optional) ships `sensor.ep_cube_import_today` / `sensor.ep_cube_export_today` derived from `sensor.ep_cube_grid_power`. Useful if you don't trust the cube's daily-counter midnight roll, or want sub-minute resolution.

---

<details>
<summary><h2>🛠️ For contributors</h2></summary>

Pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) for how, and for the licence contributions come under (MIT).

### 💡 Why This Exists

The EP Cube has **no documented local API** — no Modbus, no MQTT. All control goes through Canadian Solar's cloud via mobile-app endpoints.

One existing community integration exists ([Bobsilvio/epcube](https://github.com/Bobsilvio/epcube)) but carries no licence file, so it cannot legally be forked. This is a **clean-room build**.

**The end goal:** working Octopus Agile tariff optimisation via [Predbat](https://github.com/springfall2008/batpred). Predbat operates on a rate-based, time-windowed contract; the EP Cube exposes a mode + TOU-schedule contract. This integration includes a **shim service layer** that translates between the two.

### 🗺 Architecture

```mermaid
graph TB
    subgraph Cloud ["☁️ Canadian Solar Cloud"]
        EP[monitoring-eu.epcube.com]
    end

    subgraph HA ["🏠 Home Assistant"]
        INT[ep_cube integration]
        SHIM[Predbat shim<br/>services.py]
        STATE[predbat_state.py]
        SENS[26 sensors + control entities]
    end

    subgraph Predbat ["📈 Predbat container"]
        PB[nipar44/predbat_addon]
        OCT[Octopus Agile rates]
        SOL[Solcast PV forecast]
    end

    EP <-->|Bearer token auth<br/>silent re-auth| INT
    INT --> SENS
    PB -->|best_charge_* / best_export_* entities| STATE
    STATE --> SHIM
    SHIM -->|switchMode writes| INT
    OCT --> PB
    SOL --> PB
```

### 📁 Layout

```
ha-ep-cube/
├── custom_components/ep_cube/      ← HA integration
│   ├── services.py                 ← Predbat shim service handlers
│   └── predbat_state.py            ← Reads predbat.best_charge_* / best_export_* entities
├── mock_server/                    ← FastAPI mock of the EP Cube cloud (dev without hardware)
├── dashboards/
│   └── ep_cube.yaml                ← Lovelace dashboard (animated power flow + mode controls)
├── examples/
│   └── ha_config/                  ← Drop-in YAML for Predbat helpers + Energy dashboard rollups
├── docs/
│   ├── ARCHITECTURE.md             ← Predbat shim contract + design notes
│   ├── PREDBAT.md                  ← Predbat install + tariff (BottlecapDave) + Solcast runbook
│   ├── PHASE_3_2.md                ← Bearer-token + captcha refactor notes
│   ├── MITMPROXY_SETUP.md          ← Cloud-API capture tooling (for contributors)
│   ├── TROUBLESHOOTING.md          ← Known wire-level gotchas
│   └── predbat_apps.yaml.example   ← Predbat custom-inverter template
└── docker-compose.yml              ← HA + mock-server stack
```

### 🚀 Dev Setup

**Requires Docker.**

```bash
git clone https://github.com/SkiLtY/ha-ep-cube
cd ha-ep-cube
docker compose up -d
```

This brings up:

| Service | URL | Notes |
|---------|-----|-------|
| Home Assistant | http://localhost:8123 | `custom_components/ep_cube/` volume-mounted |
| Mock EP Cube cloud | http://localhost:8765 | FastAPI mock of the mobile-app surface |

Add the integration via HA's UI:

1. Settings → Devices & services → Add integration → *Canadian Solar EP Cube*
2. Region: **Other** (escape hatch for custom hosts)
3. Base URL: `http://mock:8765` (Docker network DNS)
4. API prefix: `/api`
5. Username + password: any string — the mock accepts anything and returns a stub Bearer token

The mock's `deviceList` resolves a single device (`devId=5613`); no manual ID needed.

### Running Tests

```bash
pip install -r requirements-test.txt
pytest
```

Tests use [`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) and run without Docker. The CI matrix (`.github/workflows/validate.yml`) runs the same on **Python 3.12** against every PR and weekly. (3.13 is pending an aiodns/pycares fix in HA's test stack.)

Coverage spans: API client (envelope unwrapping, 403→re-auth retry, `switch_mode` verification), Predbat shim (idempotency, baseline snapshotting, auto-revert), `predbat_state` parsing, config flow (region routing + migration), and entity registration.

### 🏗 HA Install Type

This stack uses **HA Container** (lightweight, no Supervisor). HA Container cannot install add-ons — Predbat runs as a sibling **`nipar44/predbat_addon`** Docker container (the upstream-recommended replacement for the now-deprecated AppDaemon install path). See [docs/PREDBAT.md](docs/PREDBAT.md).

</details>

---

## 🛣 Roadmap

| Phase | Status | What |
|-------|:------:|------|
| 1 | ✅ | Mock cloud + HA integration skeleton + 9 sensors + DeviceInfo |
| 2a | ✅ | Predbat shim: 7 services, baseline snapshot, idempotency, auto-revert |
| 2b | ✅ | Predbat as `nipar44/predbat_addon` container, plan loop validated end-to-end |
| 2b.1 | ✅ | Shim reads params from `predbat.best_charge_*` / `predbat.best_export_*` entities |
| 2c | ✅ | Live Octopus Agile rates via public REST API (no Octopus account required) |
| 2c+ | ✅ | Solcast PV forecast wired in (split E/W array) |
| 3 | ✅ | Hardware reconciliation — live cloud bring-up against `monitoring-eu.epcube.com` |
| 3.1 | ✅ | `charge_freeze` → mid-peak TOU slot; force-export gap documented; stable device name |
| 3.2 | ✅ | Mobile-app Bearer-token auth replaces JSESSIONID-cookie paste |
| 3.3 | ✅ | Animated power-flow Lovelace dashboard |
| 3.4 | ✅ | Feature-parity vs Bobsilvio/epcube — control entities + daily kWh sensors + i18n |
| 3.5 | ✅ | Bobsilvio-parity metrics expansion — 5 sensors + 4 `utility_meter` rollups |
| **4** | ✅ | **HACS distribution** — first release [`v0.5.0`](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v0.5.0) shipped 2026-05-28. 150-test pytest suite + CI matrix, demo-first README, curated release-notes workflow, brand assets self-hosted, helpers shipped. **HACS Default submission opened 2026-06-09 as [`hacs/default#8364`](https://github.com/hacs/default/pull/8364)** — all 11 bot checks green first try, awaiting maintainer merge. |
| 4.1 | ✅ | **TOU schedule editor** — shipped in [`v0.6.0`](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v0.6.0) → [`v0.7.0`](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v0.7.0). Subsequently **removed in v1.0** — see below. |
| **v1.0** | ✅ | **Opinionated Predbat bridge** — TOU editor card + `set_tou_schedule` service removed; HA Repair flow added that detects user-painted TOU slots while Predbat is installed and offers a one-click wipe. Predbat is the single source of truth for TOU. Manual TOU painting belongs in the EP Cube mobile app or [Bobsilvio/epcube](https://github.com/Bobsilvio/epcube). |
| 4.2 (Tier 1+2) | ✅ | Cloud-stats expansion — `queryDataElectricityV2` shipped in [`v1.1.0`](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v1.1.0) + [`v1.1.1`](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v1.1.1): signed grid import/export, yesterday's energy quartet, 5-min stats coordinator, ghost-entity sweep on upgrade. Patch releases [`v1.1.2`](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v1.1.2) (dashboard entity-id fix + backup-loads/EPS Gateway README clarification) + [`v1.1.3`](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v1.1.3) (hassfest compliance — drop `predbat_inverter_mode` state translation block) shipped during HACS pre-submission smoke test. |
| **v1.2** | ✅ | **Mislabel fix + cube-native rollups + instant tiles** — shipped 2026-06-10 in [`v1.2.0`](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v1.2.0). 15 new sensors (4 derived %s + 8 cube-native monthly/yearly rollups + 3 instant-KPI tiles), self-consumption sensor renamed to self-sufficiency with `unique_id` preserved for back-compat, dashboard grows a "Right now" gauge stack, `utility_meter` helpers dropped from the example package in favour of cube-native rollups. Suite 178 → 216. |
| **v1.3** | ✅ | **Dashboard refresh + signed grid net** — shipped 2026-06-12 in [`v1.3.0`](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v1.3.0). Dashboard restructured into three tabs (Now / Today / Control); Now-tab swaps SC/SS instant % gauges (low signal — both pin at 100% most of the day) for live Solar/Battery/Grid power gauges in W; Today-tab grows a signed Grid gauge + matching yesterday row using 2 new derived sensors (`grid_net_today` + `grid_net_yesterday`). SC + SS today/yesterday return `0.0` instead of `unknown` below the jitter floor (fixes "Entity is non-numeric" overlay every morning). Unified colour palette: HA Energy Dashboard's purple-out / blue-in across both signed gauges + Material green for renewables. Suite 216 → 223. |
| 4+ | ⏸️ | [HomeAssistant-OctopusEnergy](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy) — half-hourly smart-meter consumption replaces Riemann `load_today`. Gated on Octopus Home Mini arrival. |
| **v1.4** | ✅ | **Forced export and charge via Dynamic Tariff** — opt-in Dynamic-mode export and charge windows for Predbat (low sell-above, Reserve SoC from the plan, two-step revert, watchdog), the `switchMode` HTTP 500 fix, tariff selection from Home Assistant, per-string PV sensors, the *Reserve (your setting)* sensor, resilience to brief cloud errors, and the redesigned Now / Today / Yesterday / Week dashboard. See [release notes](docs/RELEASE_NOTES_v1.4.0.md). |
| **v1.5** | ✅ | **EV charger sensors + a dashboard that reconciles with your bill** — EV charger power and EV charger today (only when the cube reports a charger) and a house load that includes the car, contributed by [Eric Fisher](https://github.com/eafisher11); a Month tab with a daily billed-cost chart that matches Octopus's own page; estimates until each day is billed; solar-used-at-home rings everywhere; and a weekly package that adds up finished days instead of a utility meter that over-counted. See [release notes](docs/RELEASE_NOTES_v1.5.0.md). |
| v1.6 | 🚧 | Battery charged / discharged today counters survive a restart ([#3](https://github.com/SkiLtY/ha-ep-cube/issues/3)), plus eco metrics ([#10](https://github.com/SkiLtY/ha-ep-cube/issues/10)) and read-only Dynamic tariff threshold sensors ([#11](https://github.com/SkiLtY/ha-ep-cube/issues/11)). |
| v1.7 | 📋 | **Lifetime totals** that survive restarts ([#9](https://github.com/SkiLtY/ha-ep-cube/issues/9)) and **control without Predbat** — Dynamic threshold number entities ([#12](https://github.com/SkiLtY/ha-ep-cube/issues/12)), supported force-export and force-charge services ([#13](https://github.com/SkiLtY/ha-ep-cube/issues/13), [#14](https://github.com/SkiLtY/ha-ep-cube/issues/14)) and a fixed-hours export example ([#15](https://github.com/SkiLtY/ha-ep-cube/issues/15)). |

The full backlog, including the dashboard and Predbat-history ideas, is in the [issues](https://github.com/SkiLtY/ha-ep-cube/issues).

---

## ☕ About + Support

> **Hacking life to keep it simple. Solving technical challenges along the way.**
>
> I'm an integration engineer who loves taking complex technical puzzles and turning them into simple solutions.
>
> When I'm not at a terminal, you'll usually find me logging miles in the pool or on the bike, training for that next aquabike event.
>
> If any of my tools, scripts, or tinkering have helped you solve a challenge of your own, tossing a ko-fi in the tank helps keep the engine running. Thanks for the support! ☕

<div align="center">

[![ko-fi](https://img.shields.io/badge/Ko--fi-Buy_me_a_coffee-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/SkiLtY)

</div>

— **Michael Skilton** ([@SkiLtY](https://github.com/SkiLtY))

---

## 🙏 Acknowledgements

Finding my way around the EP Cube cloud started from the API notes in [lowsbarrel/epcube-ha](https://github.com/lowsbarrel/epcube-ha) (MIT). Several routes listed there, including the tariff and earnings ones, don't exist on the EU server; what I found while testing them, including the Dynamic-tariff (`dynamicPrice`) routes, went back upstream in [lowsbarrel/epcube-ha#14](https://github.com/lowsbarrel/epcube-ha/pull/14), which has been merged.

The EV charger sensors (and the EV share of the house load), the Home Assistant–local-midnight reset of the battery charged/discharged counters and the fix for the cloud's midnight zero reading in the battery energy come from [Eric Fisher (@eafisher11)](https://github.com/eafisher11/ha-ep-cube), who built and tested them on a US cube. Thank you.

---

## 📄 Licence

MIT — see [LICENSE](LICENSE).

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
