# mock_server

FastAPI mock of the EP Cube cloud API for development without burning real cloud sessions.

**Endpoint shapes match the real `monitoring-eu.epcube.com` / `cas-eu.epcube.com` cloud** as captured on 2026-05-20. See `<captures-private>/2026-05-20-contract-extract.md` (auth + status + mode) and `<captures-private>/2026-05-20-tou-extract.md` (TOU) for the wire-level reference. The mock is permissive about auth — the real cloud's slider-puzzle captcha is out of scope; we only need shape parity for integration dev.

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

- **Response envelope.** Every authenticated JSON response is wrapped in `{timestamp, message, status, data}`. `status: 200` on success; the actual payload is in `data`.
- **Auth.** Cookie-based, not bearer. POST to `/cas/login` (any credentials) sets `JSESSIONID=mock-session` via a 302 redirect chain mirroring the real CAS flow. Subsequent endpoints accept any cookie value (or none — auth is lenient for dev).
- **Two identifiers per device.** `devId` (small int e.g. `"5613"`) used by most endpoints; `sgSn` (21-digit serial) used only by `homeDeviceInfo`. Both are returned from `/v1/api/home/deviceList`.
- **Power values are kW as decimal strings.** E.g. `"solarPower": "1.20"`. SoC is an integer 0–100. The integration must coerce strings → numbers internally.
- **TOU schedule is price-tier based.** Three parallel tier arrays (`offPeakTimeList`, `midPeakTimeList`, `peakTimeList`) per profile (weekday / weekend / DST × 2 = 4 profiles, 12 arrays total) plus `activeWeek`-style day masks. Slots encoded as `"HH:MM_HH:MM_price"` strings. Behaviour is implicit from tier label.
- **Mode-switch is bundled with mode-specific reserve.** Three separate write endpoints: `setSelfConsumption`, `setBackUp`, `setTimOfUse`. The TOU one also saves the schedule in the same round-trip.

## Endpoints

### Auth (cookie-based, no captcha)

| Method | Path | Purpose |
|---|---|---|
| POST | `/cas/login` | Accept any creds, 302 → set `JSESSIONID` cookie. Mirrors real CAS form-login. |
| GET | `/v1/api/login/cas` | CAS callback (final redirect hop). |

### Session / metadata

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/api/system/user/getLoginUser` | Liveness probe — first authenticated XHR on the real cloud. |
| GET | `/v1/api/common/getVersion` | Backend version string. |

### Device discovery

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/api/home/deviceList` | Canonical "what devices does this account have". |
| GET | `/v1/api/device/getDeviceList?pageNum=&pageSize=` | Paginated variant with derived fields (`batteryType`, `systemCapacity`). |

### Live status

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/api/home/homeDeviceInfo?sgSn=<21-digit>` | Battery + power flow snapshot. **Keyed by sgSn, not devId.** Real cloud poll cadence ≈ 55s. |

### Mode + TOU

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/api/home/getSwitchMode?devId=<id>` | Current mode + reserves + full TOU schedule. The canonical read for both mode and schedule. |
| POST | `/v1/api/home/setSelfConsumption` | Switch to `workStatus:"1"`. Body: `{"devId","workStatus":"1","selfConsumptioinReserveSoc"}` (sic — typo preserved). |
| POST | `/v1/api/home/setBackUp` | Switch to `workStatus:"3"`. Body: `{"devId","workStatus":"3","backupPowerReserveSoc"}`. |
| POST | `/v1/api/home/setTimOfUse` | Switch to `workStatus:"2"` AND save the 16-field schedule in one POST. |
| GET | `/v1/api/home/clearTouMode?devId=<id>` | Wipe all TOU slot lists (does not switch mode). |

### Config

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/api/home/getSellingConfig/{devId}` | Export-to-grid configuration (limits, grid code, sellingEnable flag). |

## Default device state

- `devId="5613"`, `sgSn="100100007001257120126"`
- 20 kWh nominal (4× 5 kWh packs), firmware V1.2.2, EREC G99
- SoC 55 % (11.0 kWh stored), Solar 1.20 kW, Load 0.80 kW (all backUp), Grid 0.00 kW
- Mode `"1"` (Self-Consumption), reserves: self=10 %, backup=100 %, ev=50 %
- TOU schedule pre-populated weekday-only: off-peak 00:30–04:30 @ 0.05, mid 04:30–16:00 @ 0.25, peak 16:00–19:00 @ 0.40

## Dev-only endpoints (state injection + inspection)

Not part of the real cloud contract — provided for test isolation and shim verification.

| Method | Path | Purpose |
|---|---|---|
| POST | `/__sim__/device/{devId}/state` | Patch arbitrary scalar fields on the DeviceState (e.g. force SoC to 30 to test Predbat charge logic). |
| GET | `/__sim__/device/{devId}/tou-current` | Inspect the current TOU schedule as the wire would render it. Useful for verifying shim writes landed. |
| POST | `/__sim__/device/{devId}/tou-set-slots` | Build a TOU schedule from tuple-of-(start,end,price) lists without spelling out all 16 wire arrays. |
| POST | `/__sim__/device/{devId}/reset` | Reset the device to factory defaults — handy between tests. |

Examples:

```bash
# Drop SoC to 25 % to trigger a Predbat charge plan
curl -X POST http://localhost:8765/__sim__/device/5613/state \
  -H 'Content-Type: application/json' \
  -d '{"batterySoc": 25, "batteryCurrentElectricity": 5.0}'

# Inject a cheap-night TOU schedule without writing all 16 arrays
curl -X POST http://localhost:8765/__sim__/device/5613/tou-set-slots \
  -H 'Content-Type: application/json' \
  -d '{"weekday": {"offPeak": [["00:30","04:30",0.05]], "midPeak": [["04:30","16:00",0.25]], "peak": [["16:00","19:00",0.40]]}}'

# Inspect what the shim wrote after a charge_start call
curl -s http://localhost:8765/__sim__/device/5613/tou-current | python3 -m json.tool

# Get live status (no auth required — mock is lenient)
curl -s 'http://localhost:8765/v1/api/home/homeDeviceInfo?sgSn=100100007001257120126' | python3 -m json.tool

# Full auth round-trip (sets the cookie)
curl -c cookies.txt -X POST http://localhost:8765/cas/login \
  -d 'username=test&password=test' -L
curl -b cookies.txt -s http://localhost:8765/v1/api/system/user/getLoginUser | python3 -m json.tool
```

## List all routes

```bash
curl -s http://localhost:8765/openapi.json | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(m.upper(), p) for p,ms in d['paths'].items() for m in ms]"
```

Or browse `http://localhost:8765/docs` for Swagger UI.
