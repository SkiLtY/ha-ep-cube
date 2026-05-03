# mock_server

FastAPI mock of the EP Cube cloud API for development without hardware.

**Endpoint shapes are working assumptions** — to be reconciled against captured traffic from the real EP Cube mobile app once hardware arrives.

## Run locally

```bash
pip install -r requirements.txt
uvicorn mock_server.main:app --reload --port 8765
```

## Endpoints

### Production-shape (modelled on the assumed real cloud API)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/login` | — | Returns bearer token |
| GET | `/api/v1/device/{id}/status` | Bearer | Battery + power flow snapshot |
| POST | `/api/v1/device/{id}/operating-mode` | Bearer | Switch self-consumption / TOU / backup |
| POST | `/api/v1/device/{id}/tou-schedule` | Bearer | Replace TOU schedule |

Test credentials: any username/password works. Token is always `mock-token`.

Test device: `ep_cube_test_01`.

### Dev-only (state injection)

| Method | Path | Purpose |
|---|---|---|
| POST | `/__sim__/device/{id}/state` | Patch any field of the in-memory state (e.g. force SoC to 30 to test Predbat charge logic) |

Example — drop SoC to 25% to trigger a Predbat charge plan:

```bash
curl -X POST http://localhost:8765/__sim__/device/ep_cube_test_01/state \
  -H 'Content-Type: application/json' \
  -d '{"soc_pct": 25, "soc_kwh": 2.5}'
```
