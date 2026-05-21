# mock_server

FastAPI mock of the EP Cube **mobile-app** cloud API for development without burning real cloud sessions.

**Endpoint shapes mirror the contract that `custom_components/ep_cube/api.py` + `captcha.py` speak** after the Phase 3.2 refactor: `/api/open/common/*` (captcha-solver login) + `/api/device/*` (Bearer-protected polling + writes). The older web-portal surface (`/v1/api/home/*` + JSESSIONID) the mock used to mimic is gone — see `docs/PHASE_3_2.md` for wire-level discoveries.

The mock is permissive about auth: any non-empty Bearer is accepted on `/api/device/*`, the captcha images are tiny PIL-friendly placeholders the solver can run on without hanging, and `pointJson` / `captchaVerification` are NOT cryptographically validated. We only care about *shape parity*.

## Run locally

```bash
pip install -r requirements.txt
uvicorn mock_server.main:app --reload --port 8765
```

In the docker-compose stack, the service is built from `mock_server/Dockerfile` and listens on host port `8765`. **After any source change, rebuild not just restart** — the image bakes the code in:

```bash
docker compose up -d --build mock
```

## Contract summary

- **Response envelope.** Every response is wrapped in `{timestamp, message, status, data}`. `status: 200` on success; the actual payload is in `data`.
- **Auth.** Bearer token. POST to `/api/open/common/login` (any credentials) returns `{data: {token: "mock-bearer-token", ...}}`. Subsequent `/api/device/*` calls require `Authorization: Bearer <any-non-empty-token>` — missing/empty → 403.
- **Two identifiers per device.** `devId` (small int e.g. `"5613"`) used by most endpoints; `sgSn` (21-digit serial) used only by `homeDeviceInfo`. Both are returned from `/api/device/deviceList`.
- **Power values are centi-kilowatt integers.** E.g. `"solarPower": 64` means 0.64 kW = 640 W. SoC is an integer 0–100. The integration's `_power_to_w` multiplies wire value by 10 to get watts; see api.py for the empirical derivation.
- **TOU schedule is price-tier based.** Three parallel tier arrays (`offPeakTimeList`, `midPeakTimeList`, `peakTimeList`) per profile (weekday / weekend / DST × 2 = 4 profiles, 12 arrays total) plus `activeWeek`-style day masks. Slots encoded as `"HH:MM_HH:MM_price"` strings. Day arrays must be sent as `list[str]` on write; cube normalises them to `list[int]` on read.
- **Mode + TOU are one unified write.** `POST /api/device/switchMode` carries the workStatus + reserves + TOU schedule in a single body. Required fields validated: missing `weatherWatch` etc. returns `500 "The parameter cannot be null ：<field>"` (note U+FF1A fullwidth colon — cloud is China-hosted).

## Endpoints

### Captcha + login (open, no Bearer)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/open/common/captcha/get` | Returns `{repCode:"0000", repData:{secretKey, token, originalImageBase64, jigsawImageBase64}}`. Images are tiny palette PNGs. |
| POST | `/api/open/common/captcha/check` | Always returns `{repCode:"0000", repData:{result:true}}`. AES pointJson not validated. |
| POST | `/api/open/common/login` | Body `{userName, password, captchaVerification}` → `{token: "mock-bearer-token", ...}`. |

### Device endpoints (Bearer-protected)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/device/deviceList` | List devices on this account. Returns a JSON list inside `data`. |
| GET | `/api/device/homeDeviceInfo?sgSn=<21-digit>&dayMonthYearFormat=YYYY-MM-DD` | Battery + power-flow snapshot. Power fields are centi-kW ints. Status `"1"` is the device-online flag, not an envelope error. |
| GET | `/api/device/getSwitchMode?devId=<id>` | Current mode + reserves + full TOU schedule. The canonical read for both mode and schedule. |
| POST | `/api/device/switchMode` | Unified mode + TOU write. Body must include `devId`, `workStatus`, `weatherWatch`, `onlySave`; missing required field → 500. |

## Default device state

- `devId="5613"`, `sgSn="100100007001257120126"`
- 20 kWh nominal (4× 5 kWh packs), firmware V1.2.2, EREC G99
- SoC 55 % (11.0 kWh stored), Solar 1.20 kW, Load 0.80 kW (all backUp), Grid 0.00 kW
- Mode `"1"` (Self-Consumption), reserves: self=10 %, backup=100 %, ev=50 %
- TOU schedule pre-populated weekday-only: off-peak 00:30–04:30 @ 0.05, mid 04:30–16:00 @ 0.25, peak 16:00–19:00 @ 0.40

## Dev-only endpoints (state injection + inspection)

Not part of the real cloud contract — provided for test isolation and shim verification. **Not Bearer-protected** so test scripts don't need to fake a login.

| Method | Path | Purpose |
|---|---|---|
| POST | `/__sim__/device/{devId}/state` | Patch arbitrary scalar fields on the DeviceState (e.g. force SoC to 30 to test Predbat charge logic). |
| GET | `/__sim__/device/{devId}/tou-current` | Inspect the current TOU schedule as the wire would render it. Useful for verifying shim writes landed. |
| POST | `/__sim__/device/{devId}/tou-set-slots` | Build a TOU schedule from tuple-of-(start,end,price) lists without spelling out all 16 wire arrays. |
| POST | `/__sim__/device/{devId}/reset` | Reset the device to factory defaults — handy between tests. |

Examples:

```bash
# Acquire a bearer token (any creds work)
TOKEN=$(curl -s -X POST http://localhost:8765/api/open/common/login \
  -H 'Content-Type: application/json' \
  -d '{"userName":"test","password":"test","captchaVerification":"x"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['token'])")

# Drop SoC to 25 % to trigger a Predbat charge plan (no auth on __sim__)
curl -X POST http://localhost:8765/__sim__/device/5613/state \
  -H 'Content-Type: application/json' \
  -d '{"batterySoc": 25, "batteryCurrentElectricity": 5.0}'

# Inject a cheap-night TOU schedule without writing all 16 arrays
curl -X POST http://localhost:8765/__sim__/device/5613/tou-set-slots \
  -H 'Content-Type: application/json' \
  -d '{"weekday": {"offPeak": [["00:30","04:30",0.05]], "midPeak": [["04:30","16:00",0.25]], "peak": [["16:00","19:00",0.40]]}}'

# Inspect what the shim wrote after a charge_start call
curl -s http://localhost:8765/__sim__/device/5613/tou-current | python3 -m json.tool

# Live status (needs bearer)
curl -H "Authorization: Bearer $TOKEN" \
  'http://localhost:8765/api/device/homeDeviceInfo?sgSn=100100007001257120126&dayMonthYearFormat=YYYY-MM-DD' \
  | python3 -m json.tool
```

## List all routes

```bash
curl -s http://localhost:8765/openapi.json | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(m.upper(), p) for p,ms in d['paths'].items() for m in ms]"
```

Or browse `http://localhost:8765/docs` for Swagger UI.
