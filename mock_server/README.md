# mock_server

FastAPI mock of the EP Cube cloud API for development without hardware.

**Endpoint shapes are working assumptions** — to be reconciled against captured traffic from the real EP Cube mobile app once hardware arrives.

## Run locally

```bash
pip install -r requirements.txt
uvicorn mock_server.main:app --reload --port 8765
```

In the docker-compose stack, the service is built from `mock_server/Dockerfile` and listens on host port `8765`. After any source change, **rebuild not just restart**:

```bash
docker compose up -d --build mock
```

## Endpoints

### Production-shape (modelled on the assumed real cloud API)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/login` | — | Returns bearer token |
| GET | `/api/v1/device/{id}/status` | Bearer | Battery + power flow snapshot |
| GET | `/api/v1/device/{id}/tou-schedule` | Bearer | Read current TOU — used by Predbat shim for baseline snapshot |
| POST | `/api/v1/device/{id}/tou-schedule` | Bearer | Replace TOU schedule |
| POST | `/api/v1/device/{id}/operating-mode` | Bearer | Switch self-consumption / time-of-use / backup; optional `reserve_soc_pct` |

Test credentials: any username/password works. Token is always `mock-token`.
Test device: `ep_cube_test_01`.

### Default device state

- SoC 55% (5.5 kWh of 10 kWh capacity)
- Solar 1200 W, Load 800 W, Grid −100 W (exporting), Battery −300 W (discharging)
- Mode `self_consumption`, reserve SoC 20%
- 4-slot weekday TOU baseline + 1-slot weekend baseline

### Dev-only (state injection + inspection)

| Method | Path | Purpose |
|---|---|---|
| POST | `/__sim__/device/{id}/state` | Patch any field of the in-memory state (e.g. force SoC to 30 to test Predbat charge logic) |
| GET | `/__sim__/device/{id}/tou-current` | Inspect what the integration most recently wrote — useful for verifying the Predbat shim |

Examples:

```bash
# Drop SoC to 25% to trigger a Predbat charge plan
curl -X POST http://localhost:8765/__sim__/device/ep_cube_test_01/state \
  -H 'Content-Type: application/json' \
  -d '{"soc_pct": 25, "soc_kwh": 2.5}'

# Inspect what the shim wrote to TOU after a charge_start call
curl -s http://localhost:8765/__sim__/device/ep_cube_test_01/tou-current | python3 -m json.tool

# Check status (requires bearer token)
curl -s http://localhost:8765/api/v1/device/ep_cube_test_01/status \
  -H 'Authorization: Bearer mock-token' | python3 -m json.tool
```

### List all routes

The mock exposes FastAPI's auto-generated OpenAPI schema. Useful for verifying a rebuild took effect:

```bash
curl -s http://localhost:8765/openapi.json | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(m.upper(), p) for p,ms in d['paths'].items() for m in ms]"
```

Or browse `http://localhost:8765/docs` for Swagger UI.
