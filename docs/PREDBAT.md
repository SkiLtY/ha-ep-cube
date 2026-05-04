# Phase 2b — Install Predbat against our shim

Goal: get [Predbat](https://github.com/springfall2008/batpred) reading EP Cube sensors, planning charge/discharge against test prices, and calling our shim services.

## Why a sibling container

Predbat is fundamentally an [AppDaemon](https://appdaemon.readthedocs.io/) app. The "Predbat add-on" you see in HA OS is just AppDaemon + Predbat code packaged together. We're on **HA Container** which has no Supervisor and therefore no Add-on store, so we run AppDaemon as its own Docker service alongside HA + mock.

This is actually a cleaner architecture for our setup:
- AppDaemon talks to HA over REST + WebSocket using a long-lived access token
- Predbat is just files in `appdaemon_config/apps/predbat/`
- Version-pin Predbat independently of HA Container's release cycle
- `docker compose pull` updates AppDaemon's image; `git pull` in the Predbat clone updates Predbat itself

## Prerequisites checklist

- [x] Phase 1 + 2a complete (HA + mock running, integration loaded, 9 sensors visible, 7 shim services working)
- [ ] HA long-lived access token generated (steps below)
- [ ] Predbat repo cloned on <host>

## Steps

### 1. Generate a Home Assistant long-lived access token

In the HA UI:

1. Click your profile (bottom-left) → **Security** tab
2. Scroll to **Long-lived access tokens** → **Create token**
3. Name it `appdaemon`
4. Copy the token *immediately* — it's shown only once

Store it in `appdaemon_config/secrets.yaml` (created in step 3).

### 2. AppDaemon service in `docker-compose.yml`

Already wired in this repo. Two non-obvious choices baked into the compose file:

- **AppDaemon is pinned to `4.4.2` via a custom Dockerfile** (`./appdaemon/Dockerfile`). 4.5.x added strict topological dependency sorting that rejects Predbat's internal Python import cycles (`predbat ↔ hass ↔ userinterface`). The custom image extends `acockburn/appdaemon:4.4.2` and pre-installs Predbat's runtime deps with versions compatible with AppDaemon 4.4.2's Python 3.10 and aiohttp 3.8.x — Predbat's own `requirements.txt` pins newer numpy/aiohttp/pytz/requests that break AppDaemon's HASS plugin.
- **Predbat clone is bind-mounted at `/predbat-source` (outside `/conf`).** AppDaemon's startup script recursively scans `/conf` for `requirements.txt` files and pip-installs them. By keeping the Predbat clone out of `/conf`, that step skips it. The Python module is exposed inside `/conf/apps/predbat` via a symlink to `/predbat-source/apps/predbat`.

### 3. `appdaemon_config/` layout

Already committed. Final structure on the host:

```
ha-ep-cube/
├── appdaemon/
│   └── Dockerfile             ← extends acockburn/appdaemon:4.4.2 + pre-installs Predbat deps
├── appdaemon_config/
│   ├── appdaemon.yaml         ← HA plugin + admin UI + http port
│   ├── secrets.yaml           ← HA long-lived token (gitignored)
│   ├── secrets.yaml.example   ← committed template
│   └── apps/
│       ├── apps.yaml          ← Predbat config — uses our ep_cube.* services
│       └── predbat -> /predbat-source/apps/predbat   ← symlink (resolved inside container)
└── predbat-source/            ← Cloned from springfall2008/batpred (gitignored)
```

**`appdaemon.yaml`:**

```yaml
---
secrets: /conf/secrets.yaml
appdaemon:
  latitude: 51.5074
  longitude: -0.1278
  elevation: 30
  time_zone: Europe/London
  plugins:
    HASS:
      type: hass
      ha_url: http://homeassistant:8123
      token: !secret ha_token
http:
  url: http://0.0.0.0:5050
admin:
api:
hadashboard:
```

**`secrets.yaml`** (gitignore this — already covered by the existing `.env` rules but add `appdaemon_config/secrets.yaml` to `.gitignore` explicitly):

```yaml
ha_token: <paste-the-long-lived-token>
```

**`apps/apps.yaml`** is committed with hardcoded test rates suitable for the Phase 2b dev loop. To re-key it for your real `device_id` (when not in sim), search/replace `ep_cube_test_01` throughout. Once an Octopus account is live (Phase 2c), comment out `rates_import:` and uncomment the `metric_octopus_*` lines.

### 4. Clone Predbat code

Clone the upstream repo at the **repo root** (not under `appdaemon_config/`) and link the Python module into the AppDaemon apps directory:

```bash
cd /volume1/docker/ha-ep-cube
git clone https://github.com/springfall2008/batpred predbat-source

# Symlink uses an absolute container path — it resolves inside the container,
# not on the host. Don't worry that the host path doesn't exist.
ln -s /predbat-source/apps/predbat appdaemon_config/apps/predbat
```

`predbat-source/` and `appdaemon_config/apps/predbat` are both gitignored — managed independently from this repo. Future Predbat updates: `cd predbat-source && git pull && cd .. && sudo docker compose restart appdaemon`.

### 5. Bring up AppDaemon

```bash
cd /volume1/docker/ha-ep-cube
git pull   # picks up the new docker-compose.yml service
docker compose up -d
docker compose logs -f appdaemon
```

Watch for:
- `Connected to Home Assistant` (auth working)
- `Loading App: predbat` (Predbat module loaded)
- Predbat plan output every few minutes — slot decisions, expected SoC trajectory

### 6. Verify the loop

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

Expect a TOU schedule with one or more `_predbat_override: true` slots covering the cheap windows from your hardcoded `rates_import`.

## Likely issues

| Symptom | Likely cause | Fix |
|---|---|---|
| AppDaemon: `Authentication failed` | Wrong token or expired | Regenerate token, update `secrets.yaml`, restart appdaemon |
| AppDaemon: `Cannot connect to HA at http://homeassistant:8123` | Network/DNS mismatch | Ensure all services are in the same compose project; check `docker compose ps` |
| Predbat: `Inverter not found` or sensor values are `unknown` | Entity IDs in `apps.yaml` don't match reality | Cross-check entity IDs in HA Developer Tools → States. Format is `sensor.ep_cube_<device_id>_<key>` |
| Predbat: `service ep_cube.charge_start not found` | Services not registered (HA wasn't restarted after pulling Phase 2a code) | `docker compose restart homeassistant`, then in HA Developer Tools → Actions, type `ep_cube` and verify all 9 services appear |
| Predbat plans correctly but no service calls fire | Check Predbat is in active mode, not "predict only" | In `apps.yaml` set `set_charge_window: True` and `set_discharge_window: True` |
| Predbat keeps re-issuing identical plans | Expected — our shim's `_matches_active` idempotency check returns no-op for repeats. Check HA log for `idempotent no-op (same args as active override)` debug messages | Working as designed |

## When Octopus account is live (Phase 2c)

1. Install [BottlecapDave's HACS integration](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy)
2. Configure it with your Octopus account API key + MPAN/serial
3. In `appdaemon_config/apps/apps.yaml`:
   - Comment out the hardcoded `rates_import:` block
   - Uncomment the `metric_octopus_import:` and `metric_octopus_export:` lines and fill in your MPAN/serial
4. `docker compose restart appdaemon`

Predbat will now plan against real 30-min Agile prices.

## What's deliberately not in this guide

- **HACS install** — Phase 4. We don't need HACS to run the integration; we volume-mount it directly.
- **Tests** — Phase 4. Mock + smoke-test loop is enough for now.
- **Production hardening** — `secrets.yaml` will need encrypting before public repo, AppDaemon admin UI should be behind auth, etc. Cross those bridges when Phase 4 makes the repo public.
