# Phase 2b — Install Predbat against our shim

Goal: get [Predbat](https://github.com/springfall2008/batpred) reading EP Cube sensors, planning charge/discharge against test prices, and calling our shim services.

## Why a sibling container — and why not AppDaemon

Historically Predbat shipped as an [AppDaemon](https://appdaemon.readthedocs.io/) app. Predbat upstream has since **retired the AppDaemon install path** in favour of:

1. The **Predbat HA add-on** (only works on HA OS / Supervised), or
2. A **standalone Docker image** (`nipar44/predbat_addon`) — the route we use.

We're on HA Container, which has no Supervisor and therefore no add-on store, so the Docker image is the right fit. Earlier revisions of this guide tried to run Predbat inside a custom AppDaemon container; that path ran into incompatible `Hass.__init__` signatures, import cycles, and version-pinned numpy/aiohttp churn. None of that applies any more — `nipar44/predbat_addon` is a self-contained image with the right Predbat ↔ runtime contract baked in.

## Prerequisites checklist

- [x] Phase 1 + 2a complete (HA + mock running, integration loaded, 9 sensors visible, 7 shim services working)
- [ ] HA long-lived access token generated (steps below)

## Steps

### 1. Generate a Home Assistant long-lived access token

In the HA UI:

1. Click your profile (bottom-left) → **Security** tab
2. Scroll to **Long-lived access tokens** → **Create token**
3. Name it `predbat`
4. Copy the token *immediately* — it's shown only once

### 2. Predbat service in `docker-compose.yml`

Already wired in this repo:

```yaml
predbat:
  image: nipar44/predbat_addon:alpine-latest
  container_name: ha-ep-cube-predbat
  restart: unless-stopped
  ports:
    - "5052:5052"
  volumes:
    - ./predbat_config:/config:rw
  depends_on:
    - homeassistant
  environment:
    - TZ=Europe/London
```

Predbat web UI ends up at `http://<host>:5052/`.

### 3. `predbat_config/` layout

Final structure on the host:

```
ha-ep-cube/
└── predbat_config/
    ├── apps.yaml              ← Predbat config (committed)
    ├── secrets.yaml.example   ← committed template
    ├── secrets.yaml           ← HA long-lived token (gitignored)
    └── predbat.log            ← Predbat's runtime log (gitignored)
```

`apps.yaml` is committed with hardcoded test rates suitable for the dev loop. The HA connection lives at the top of the `pred_bat:` block:

```yaml
pred_bat:
  ha_url: 'http://homeassistant:8123'
  ha_key: !secret ha_key
  ...
```

`secrets.yaml` is just:

```yaml
ha_key: <paste-the-long-lived-token>
```

### 4. Bring up Predbat

On <host>:

```bash
cd /volume1/docker/ha-ep-cube
git pull
cp predbat_config/secrets.yaml.example predbat_config/secrets.yaml
# edit predbat_config/secrets.yaml — paste the token
docker compose up -d
docker compose logs -f predbat
```

Watch for:
- HA WebSocket auth success
- Predbat plan output every few minutes — slot decisions, expected SoC trajectory
- Service calls being issued against `ep_cube.charge_start` etc.

### 5. Verify the loop

Force conditions that should trigger a Predbat charge plan:

```bash
# Drop battery SoC to 20% so Predbat sees a need to charge during cheap slots
curl -X POST http://localhost:8765/__sim__/device/ep_cube_test_01/state \
  -H 'Content-Type: application/json' \
  -d '{"soc_pct": 20, "soc_kwh": 2.0}'
```

Watch HA logs for `ep_cube.charge_start` being called by Predbat (HA UI → Settings → System → Logs, or `docker compose logs homeassistant | grep ep_cube`).

Then inspect what landed on the mock:

```bash
curl -s http://localhost:8765/__sim__/device/ep_cube_test_01/tou-current | python3 -m json.tool
```

Expect a TOU schedule with one or more `_predbat_override: true` slots covering the cheap windows from `rates_import`.

## Likely issues

| Symptom | Likely cause | Fix |
|---|---|---|
| Predbat log: `Authentication failed` / `401` | Wrong token or expired | Regenerate token, update `secrets.yaml`, `docker compose restart predbat` |
| Predbat: `Cannot connect to HA at http://homeassistant:8123` | Network/DNS mismatch | All services must be in the same compose project; check `docker compose ps` |
| Predbat: sensor values `unknown` or `Inverter not found` | Entity IDs in `apps.yaml` don't match reality | Cross-check entity IDs in HA Developer Tools → States. Format is `sensor.ep_cube_<device_id>_<key>` |
| Predbat: `service ep_cube.charge_start not found` | Services not registered after pulling Phase 2a code | `docker compose restart homeassistant`, then HA Developer Tools → Actions → type `ep_cube` and verify the 9 services |
| Predbat plans correctly but no service calls fire | Predbat in "predict only" mode | In `apps.yaml` set `set_charge_window: True` and `set_discharge_window: True` |
| Predbat keeps re-issuing identical plans | Expected — shim's `_matches_active` idempotency returns no-op | Working as designed; check HA log for `idempotent no-op` debug messages |

## Tariff (Phase 2c — done)

`apps.yaml` is already wired to fetch Octopus Agile rates from the public REST API — no Octopus account needed:

```yaml
rates_import_octopus_url: "https://api.octopus.energy/v1/products/AGILE-24-10-01/electricity-tariffs/E-1R-AGILE-24-10-01-L/standard-unit-rates"
rates_export_octopus_url: "https://api.octopus.energy/v1/products/AGILE-OUTGOING-19-05-13/electricity-tariffs/E-1R-AGILE-OUTGOING-19-05-13-L/standard-unit-rates/"
```

Region letter at the end of each URL (`-L`) is the DNO region — change to match yours. Octopus rotates the import product code (`AGILE-24-10-01`) every ~year; when that happens, list current codes via:

```bash
curl -s 'https://api.octopus.energy/v1/products/?brand=OCTOPUS_ENERGY' \
  | jq '.results[] | select(.code | test("AGILE")) | .code'
```

[BottlecapDave's integration](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy) is a Phase 4+ upgrade once Phase 3 (hardware) is verified. It brings in real half-hourly smart-meter consumption (replacing the Riemann-integral `sensor.ep_cube_load_today` we ship in the package) and IOG dispatch metadata. The Agile rate path stays on the public URL either way — the upgrade is additive.

## PV forecast — Solcast

Predbat needs a half-hourly PV forecast to schedule charges around free solar. Without one, the plan shows `PV kWh: 0.0` and Predbat assumes worst-case zero generation. We use [Solcast](https://solcast.com/) — free tier, 10 API calls/day, accurate.

The plumbing in `apps.yaml` (`pv_forecast_today` / `_tomorrow` / `_d3` / `_d4`) auto-discovers Solcast sensors via regex, so once the HACS integration is installed there's nothing to edit on the Predbat side — just restart it.

### One-time setup

1. **Sign up** at [solcast.com/free-rooftop-solar-forecast](https://solcast.com/free-rooftop-solar-forecast). Personal use is free, capped at 10 API polls/day across all sites.

2. **Configure your rooftop site** on Solcast's web UI:
   - Latitude/longitude (your house — Solcast accepts any UK location)
   - DC capacity (kWp) — the array's nameplate rating
   - Tilt (typical UK roof: 30–40°)
   - Azimuth (180° = due south, 90° = east, 270° = west)
   - Tracking type (almost always "Fixed")

   If hardware specs aren't finalised yet, use placeholders (e.g. 4 kWp, 35° tilt, 180° azimuth) and refine when the EP Cube + panels are commissioned.

3. **Note your credentials:**
   - **API key** — Account → API Key (single string)
   - **Resource ID** — under each site, a UUID like `aaaa-bbbb-…`

4. **Install the HACS integration:**
   - In HA: HACS → Integrations → ⋮ → "Custom repositories" is *not* needed — `BJReplay/ha-solcast-solar` is in the default HACS repository.
   - Search for "Solcast PV Forecast" → Download → restart HA.
   - Settings → Devices & Services → Add Integration → "Solcast PV Forecast" → paste API key.
   - The integration auto-discovers sites tied to the API key.

5. **Restart Predbat** (`docker compose restart predbat` on <host>) so it picks up the new `sensor.solcast_pv_forecast_*` entities. Within one plan cycle (~5 min) the Plan tab's `PV kWh` column should show non-zero values.

### What entities Solcast creates (so you know what to expect)

| Entity | Unit | Purpose |
|---|---|---|
| `sensor.solcast_pv_forecast_forecast_today` | kWh | Daily total today |
| `sensor.solcast_pv_forecast_forecast_tomorrow` | kWh | Daily total tomorrow |
| `sensor.solcast_pv_forecast_forecast_day_3` … `_day_7` | kWh | Disabled by default; enable in HA UI if you want >2-day lookahead |
| `sensor.solcast_pv_forecast_forecast_this_hour` / `_next_hour` | Wh | Half-hour-resolution short horizon |
| `sensor.solcast_pv_forecast_forecast_next_x_hours` | Wh | Custom horizon |

Predbat reads `detailedForecast` attributes off the daily totals for half-hour granularity.

### Rate-limit awareness

10 polls/day = ~one every 2.5 hours. The integration auto-paces. Don't manually trigger updates more often or you'll exhaust the quota and Solcast will return 429 until midnight UTC.

## What's deliberately not in this guide

- **HACS install** — Phase 4. We don't need HACS to run the integration; we volume-mount it directly.
- **Tests** — Phase 4. Mock + smoke-test loop is enough for now.
- **Production hardening** — `secrets.yaml` will need encrypting before public repo, Predbat web UI should be behind auth, etc. Cross those bridges when Phase 4 makes the repo public.
