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
sudo docker compose up -d
sudo docker compose logs -f predbat
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

Watch HA logs for `ep_cube.charge_start` being called by Predbat (HA UI → Settings → System → Logs, or `sudo docker compose logs homeassistant | grep ep_cube`).

Then inspect what landed on the mock:

```bash
curl -s http://localhost:8765/__sim__/device/ep_cube_test_01/tou-current | python3 -m json.tool
```

Expect a TOU schedule with one or more `_predbat_override: true` slots covering the cheap windows from `rates_import`.

## Likely issues

| Symptom | Likely cause | Fix |
|---|---|---|
| Predbat log: `Authentication failed` / `401` | Wrong token or expired | Regenerate token, update `secrets.yaml`, `sudo docker compose restart predbat` |
| Predbat: `Cannot connect to HA at http://homeassistant:8123` | Network/DNS mismatch | All services must be in the same compose project; check `sudo docker compose ps` |
| Predbat: sensor values `unknown` or `Inverter not found` | Entity IDs in `apps.yaml` don't match reality | Cross-check entity IDs in HA Developer Tools → States. Format is `sensor.ep_cube_<device_id>_<key>` |
| Predbat: `service ep_cube.charge_start not found` | Services not registered after pulling Phase 2a code | `sudo docker compose restart homeassistant`, then HA Developer Tools → Actions → type `ep_cube` and verify the 9 services |
| Predbat plans correctly but no service calls fire | Predbat in "predict only" mode | In `apps.yaml` set `set_charge_window: True` and `set_discharge_window: True` |
| Predbat keeps re-issuing identical plans | Expected — shim's `_matches_active` idempotency returns no-op | Working as designed; check HA log for `idempotent no-op` debug messages |

## When Octopus account is live (Phase 2c)

1. Install [BottlecapDave's HACS integration](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy)
2. Configure it with your Octopus account API key + MPAN/serial
3. In `predbat_config/apps.yaml`:
   - Comment out the hardcoded `rates_import:` block
   - Uncomment the `metric_octopus_import:` and `metric_octopus_export:` lines and fill in your MPAN/serial
4. `sudo docker compose restart predbat`

Predbat will now plan against real 30-min Agile prices.

## What's deliberately not in this guide

- **HACS install** — Phase 4. We don't need HACS to run the integration; we volume-mount it directly.
- **Tests** — Phase 4. Mock + smoke-test loop is enough for now.
- **Production hardening** — `secrets.yaml` will need encrypting before public repo, Predbat web UI should be behind auth, etc. Cross those bridges when Phase 4 makes the repo public.
