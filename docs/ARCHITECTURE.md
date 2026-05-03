# Architecture

## Layers

```
┌──────────────────────────────────────────────────────────────────┐
│ Predbat (HA add-on)                                              │
│   - reads Octopus Agile prices                                   │
│   - calls integration services to schedule charge/discharge      │
└────────────────────────────────┬─────────────────────────────────┘
                                 │  HA service calls
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│ custom_components/ep_cube/                                       │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │ Predbat shim (services.py)                               │   │
│   │   ep_cube.charge_start / charge_stop / ...               │   │
│   │   translates rate+window → TOU schedule rewrite          │   │
│   └────────────────────────┬─────────────────────────────────┘   │
│                            ▼                                     │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │ Native EP Cube services                                  │   │
│   │   ep_cube.set_operating_mode / set_tou_schedule          │   │
│   │   1:1 with cloud API                                     │   │
│   └────────────────────────┬─────────────────────────────────┘   │
│                            ▼                                     │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │ api.py — async HTTP client                               │   │
│   │   auth, token refresh, poll loop, write ops              │   │
│   └────────────────────────┬─────────────────────────────────┘   │
└────────────────────────────┼─────────────────────────────────────┘
                             ▼
                   ┌─────────────────────┐
                   │ EP Cube cloud API   │  ← mock_server/ during dev
                   │ (or mock server)    │     real endpoint post-hardware
                   └─────────────────────┘
```

## Predbat shim contract

Predbat ([docs](https://springfall2008.github.io/batpred/inverter-setup/)) drives an inverter via a rate-based, time-windowed contract. EP Cube's cloud surface is mode + TOU schedule. The shim is the translation layer.

### Services exposed to Predbat

| Service | Predbat input | Shim translation |
|---|---|---|
| `ep_cube.charge_start` | `rate_w`, `end_time`, `target_soc` | Rewrite TOU: insert grid-charge slot ending at `end_time`, target SoC. **`rate_w` ignored** — TOU charges at max. To time-extend a charge, shift slot start later (charge ends near `end_time` at max rate). |
| `ep_cube.charge_stop` | — | Remove the active grid-charge slot. Restore previous TOU. |
| `ep_cube.discharge_start` | `rate_w`, `end_time`, `target_soc` | Rewrite TOU: discharge-to-grid slot. **Needs hardware verification** — may require operating-mode flip. |
| `ep_cube.discharge_stop` | — | Remove discharge slot. |
| `ep_cube.charge_freeze` | `end_time` | Set reserve_soc = current SoC; mode hold. |
| `ep_cube.discharge_freeze` | `end_time` | Same as charge_freeze (battery idle). |
| `ep_cube.idle` | — | Restore default schedule. |

### State the shim must hold

- **Saved baseline TOU schedule** — so we can restore the user's normal schedule after a Predbat slot ends.
- **Active override** — the slot currently being held against cloud. Idempotent: if Predbat repeats `charge_start` with the same args, no cloud write.
- **Pending revert** — scheduled HA timer to restore baseline at slot end, in case Predbat doesn't call `charge_stop`.

### Latency + rate-limit strategy

Every override is one cloud write. Predbat re-plans every ~5 min. Naïve push hammers the cloud.

**Rule:** only push when the *next 30-min slot decision* changes. Most replans are no-ops at the inverter level.

Bound: target ≤ 12 cloud writes/day in normal operation.

## Native services

These map 1:1 to cloud endpoints and are exposed for advanced users + the shim itself.

| Service | Cloud endpoint (working assumption) |
|---|---|
| `ep_cube.set_operating_mode` | `POST /device/operating-mode` |
| `ep_cube.set_tou_schedule` | `POST /device/tou-schedule` |

Endpoint shapes are placeholders. Reconcile against captured traffic when hardware arrives.

## Cloud API client (`api.py`)

- `aiohttp.ClientSession`, async throughout (HA convention).
- Bearer token auth; refresh on 401.
- Configurable base URL — points at `mock_server` during dev, real endpoint in prod.
- Retry on 5xx with exponential backoff (max 3 attempts).
- Surface errors as typed exceptions (`AuthError`, `RateLimitError`, `ServerError`) so the shim can decide policy.

## Coordinator

Standard HA `DataUpdateCoordinator`. Polls `/device/status` every 30s. Pushes parsed state to all entities. One coordinator per EP Cube device.

## Entities (read)

- `sensor.ep_cube_battery_soc` — %
- `sensor.ep_cube_battery_soc_kwh` — kWh
- `sensor.ep_cube_battery_capacity_kwh` — kWh
- `sensor.ep_cube_battery_power` — W (signed: + charge, − discharge)
- `sensor.ep_cube_grid_power` — W (signed: + import, − export)
- `sensor.ep_cube_solar_power` — W
- `sensor.ep_cube_load_power` — W
- `sensor.ep_cube_operating_mode` — enum
- `sensor.ep_cube_reserve_soc` — %

Entity IDs match Predbat's expected naming so a custom-inverter `apps.yaml` can wire up cleanly.

## Open questions (tracked, not blocking)

- **Force discharge to grid (export)** — TOU slot or operating-mode flip? Verify on hardware.
- **Charge rate control** — confirmed not in TOU. Workaround: shift window-start. Acceptable for Octopus Agile use case.
- **Cloud API rate limits** — unknown. Assume 60 req/min until measured.
- **Token refresh interval** — unknown. Assume 24h until measured.
- **Reserve SoC max value** — assume 100%, verify.

## Hardware bring-up plan (3-week timeline)

| Phase | Duration | Goal |
|---|---|---|
| 1. Mock + skeleton | week 1 | Mock server live, HA integration loads, config flow works, sensors populated from mock |
| 2. Predbat shim | week 2 | Shim services callable, idempotency + revert logic, Predbat custom-inverter template |
| 3. Hardware reconciliation | week 3 | Capture real cloud API traffic via mitmproxy, reconcile mock contract, switch to live, fix what breaks |
