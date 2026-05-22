# Architecture

## Layers

```
┌──────────────────────────────────────────────────────────────────┐
│ Predbat (nipar44/predbat_addon Docker container — Phase 2b ✅)   │
│   - reads Octopus Agile prices via public REST API (Phase 2c)    │
│   - reads Solcast PV forecast (split E/W array, Phase 2c+)       │
│   - publishes plan to predbat.best_charge_* / best_export_*     │
│   - calls integration services to schedule charge/discharge      │
└────────────────────────────────┬─────────────────────────────────┘
                                 │  HA service calls (REST/WebSocket)
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
│   │ api.py — async aiohttp client                            │   │
│   │   auth, token refresh, poll loop, GET/POST tou-schedule  │   │
│   │   typed errors: AuthError / RateLimitError / ServerError │   │
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

### How Predbat publishes its plan (Phase 2b.1)

For `has_service_api: True` inverters, Predbat writes the planned window to entities under the `predbat.*` domain **before** firing the matching service call. Service calls themselves carry empty `data {}` (apps.yaml has no template `data:` keys), so the shim ignores call args and reads the plan from the entities.

Entity IDs are bare names (no `inverter_type` / `index` prefix — the `EP_CUBE` / `0` apps.yaml settings do not decorate these). We read the **"best" plan** Predbat has chosen, never the unprefixed `predbat.charge_*` baseline (which is the predicted no-action future).

Entities consumed by the shim (parsed in [predbat_state.py](../custom_components/ep_cube/predbat_state.py)):

| Entity ID | Source | Meaning |
|---|---|---|
| `predbat.best_charge_start` / `predbat.best_charge_end` | `attributes.timestamp` (ISO `%Y-%m-%dT%H:%M:%S%z`) | Planned charge window |
| `predbat.best_charge_limit` | `state` (int 0–100) | Target SoC for charge |
| `predbat.best_export_start` / `predbat.best_export_end` | `attributes.timestamp` (ISO) | Planned export/discharge window |
| `predbat.best_export_limit` | `state` (int 0–100) | Export-stop SoC floor (0 = full discharge, 100 = no export) |

No-plan sentinel: upstream sets `state=""` and `attributes.timestamp=None` when no charge/export is planned within the forecast horizon (`apps/predbat/output.py:2273-2276 / 2102-2105`). The shim treats either as `PredbatWindow=None` and the corresponding `*_enabled` flag as False.

Idle window and reserve SoC are not exposed as published entities upstream and are not acted on by the shim. Charge-active / export-active flags (`scheduled_charge_enable` etc.) likewise don't exist as window-scoped entities — the shim derives `charge_enabled` / `discharge_enabled` from whether the corresponding `best_*_start` has a non-empty timestamp.

Single-device assumption holds at the apps.yaml layer (`num_inverters: 1`); the published entity IDs themselves carry no device discriminator, so multi-device support is deferred to Phase 4.

### Services exposed to Predbat

All shim services accept only an optional `device_id`. The window/SoC parameters come from the entities above.

| Service | Plan inputs read | Shim translation | Status |
|---|---|---|---|
| `ep_cube.charge_start` | `best_charge_start`/`_end`, `best_charge_limit` | Set mode=TOU. Insert grid-charge slot covering now → best_charge_end, target SoC = best_charge_limit. Charge rate not in TOU surface. | ✅ verified on mock (Phase 2a flow) |
| `ep_cube.charge_stop` | — | Restore baseline mode + schedule. Cancel revert timer. | ✅ verified on mock |
| `ep_cube.discharge_start` | `best_export_start`/`_end`, `best_export_limit` | TOU peak slot covering now → best_export_end. Vendor-confirmed peak = "drain to loads, refuse grid import" — **NOT** active export. Best-effort approximation of Predbat's force-export intent. See "Known limitation: force-export" below. | ⚠️ semantic gap documented |
| `ep_cube.discharge_stop` | — | Same as charge_stop. | ✅ verified on mock |
| `ep_cube.charge_freeze` | `best_charge_start`/`_end` | TOU mid-peak slot covering now → best_charge_end. Vendor-confirmed mid-peak = "not charging, not supporting loads" — genuinely idle (no solar → battery either). | ✅ verified on mock |
| `ep_cube.discharge_freeze` | `best_export_start`/`_end` | Alias for charge_freeze, end-time taken from export window. | ✅ verified on mock |
| `ep_cube.idle` | — | Restore baseline. Equivalent to `*_stop` for any active override. | ✅ verified on mock |

If `*_start` or `*_freeze` fires while Predbat reports no plan (empty-state / null timestamp on the relevant `best_*` entities), the handler logs a warning and no-ops. This guards against stale fires during shutdown / config errors.

### State the shim holds (per device)

- **`_baseline_mode` / `_baseline_schedule`** — captured on first override per shim lifetime (lazy snapshot). Persists across overrides until shim teardown.
- **`_active_override`** — dict describing what's currently in flight. `None` when idle. Used for idempotency.
- **`_revert_unsub`** — cancellation handle for the auto-revert timer (`async_track_point_in_utc_time`).

### Idempotency

Every public service first calls `_matches_active(params)`. If the same effective parameters are already in flight, no cloud write — return immediately. This is the per-call defence against Predbat re-issuing the same plan every 5 minutes.

### Schedule merge logic

`_build_override_schedule` inserts the override into the day's baseline slots:
1. For each baseline slot, check overlap with the override.
2. If no overlap, keep as-is.
3. If overlap, keep the non-overlapping flanks (split around the override).
4. Append the override slot.
5. Sort by start time.

**Limitation v1:** string compares on `HH:MM`. Doesn't handle midnight-spanning slots correctly. Predbat 30-min slots in practice never span midnight, so acceptable for now.

### Auto-revert

Each `*_start` schedules a one-shot `async_track_point_in_utc_time` at `end_time`. The callback restores baseline. Cancellation handle is replaced on every new override and cleared on every revert.

Why: if Predbat doesn't call `*_stop` (network blip, restart, bug), the override would otherwise stay live indefinitely — bad for the user's bill.

### Latency + rate-limit strategy

Every override is one cloud write. Predbat re-plans every ~5 min. Naïve push hammers the cloud.

**Rule:** only push when the *next 30-min slot decision* changes. Most replans are no-ops at the inverter level thanks to the `_matches_active` idempotency check.

Bound: target ≤ 12 cloud writes/day in normal operation. Validated against the verified shim flow on the mock.

### Known limitation: force-export

Predbat's `discharge_start` conceptually means "push battery → grid at this rate". The EP Cube cloud API exposes no command for this. Its three operating modes (self-consumption / TOU / backup) and TOU's three tiers (off-peak / mid-peak / peak) all describe behaviour relative to *household loads*, not the grid meter:

- **off-peak** — charge from grid, ignore loads
- **mid-peak** — idle (no grid, no load support)
- **peak** — drain to loads, refuse grid import

There is no "peak + export surplus to grid at commanded rate" mode. The shim maps `discharge_start → peak` as the closest available, on the basis that:
1. Battery output up to load demand will be consumed by the house (saves import).
2. Any surplus above load *may* export if `sellingEnable` permits and the inverter chooses to — but this is implicit, not commanded.

For the Octopus Agile arbitrage use case this is usually fine — Predbat's discharge windows align with peak import prices when the house is also drawing load, so battery → loads still captures most of the value. The gap matters for "export windows during low household load" (e.g. midday plunge-pricing while you're at work) where peak mode will under-deliver versus a true force-export.

Workarounds considered (none implemented):
- **Local Modbus/RS485** — EP Cube has no exposed local API.
- **Hidden settings** (`selfHelpRate`, `winterMode` seen in JS bundle, unexplored).
- **Accept the gap** — current path. Documented here so it doesn't get re-discovered as a bug.

## Native services

These map 1:1 to cloud endpoints. Exposed for advanced users + the shim itself.

| Service | Cloud endpoint (working assumption) |
|---|---|
| `ep_cube.set_operating_mode` | `POST /api/v1/device/{id}/operating-mode` |
| `ep_cube.set_tou_schedule` | `POST /api/v1/device/{id}/tou-schedule` |

Endpoint shapes are placeholders. Reconcile against captured traffic when hardware arrives.

## Cloud API client (`api.py`)

- `aiohttp.ClientSession` injected via `async_get_clientsession(hass)`.
- Bearer token auth; auto re-auth on 401 with `_retried_auth` guard against loops.
- Configurable base URL — `http://mock:8765` in dev, real endpoint in prod.
- Methods: `authenticate`, `get_status`, `get_tou_schedule`, `set_operating_mode`, `set_tou_schedule`. Public `device_id` property for the shim resolver.
- Typed exceptions (`AuthError`, `RateLimitError`, `ServerError`, `EPCubeError`) so the shim can decide retry policy per error class.
- Retry on 5xx not yet implemented — current behaviour is bubble + let HA service-call layer handle. Add when hardware data shows it's needed.

## Coordinator

Standard HA `DataUpdateCoordinator`. Polls `/api/v1/device/{id}/status` every 30s (`DEFAULT_POLL_INTERVAL_SECONDS`). One coordinator per EP Cube device. Sensors all reference `coordinator.data` via `value_fn` lambdas.

## Entities (read)

All grouped under a single `DeviceInfo` per EP Cube. Device name format: `EP Cube {device_id}`, manufacturer Canadian Solar, model EP Cube.

| Entity (with translation_key) | Unit | device_class | Notes |
|---|---|---|---|
| `battery_soc` | % | BATTERY | |
| `battery_soc_kwh` | kWh | ENERGY_STORAGE | HA 2024.4+ |
| `battery_capacity_kwh` | kWh | — | |
| `battery_power` | W | POWER | Signed: `+` charge, `−` discharge |
| `grid_power` | W | POWER | Signed: `+` import, `−` export |
| `solar_power` | W | POWER | |
| `load_power` | W | POWER | |
| `operating_mode` | — | — | Enum string (`self_consumption`, `time_of_use`, `backup`) |
| `reserve_soc` | % | — | |

Entity IDs follow `sensor.ep_cube_{device_id}_{translation_key}` thanks to DeviceInfo. Predbat's `apps.yaml` uses these IDs directly — see `docs/predbat_apps.yaml.example`.

## Translations

Both `strings.json` (source) and `translations/en.json` (runtime) must exist. HA loads `translations/<lang>.json` at runtime — `strings.json` alone is not enough. Initial mistake in Phase 1 caused entities to fall back to device-class labels (e.g. "Power" × 4) — fixed in commit `911f584`.

## Open questions (tracked, not blocking)

| Question | Status | When to resolve |
|---|---|---|
| Force discharge to grid (export) — TOU slot or operating-mode flip? | **Resolved (Phase 3.1):** neither — the cloud API has no force-export command. Shim maps `discharge_start → peak` as best-effort. See "Known limitation: force-export". | Closed |
| Charge rate control | Confirmed not in TOU surface. Workaround: shift window-start. | Acceptable — Octopus Agile use case rarely needs sub-max charging |
| Cloud API rate limits | Unknown. Assume 60 req/min until measured. | Phase 3 |
| Token refresh interval | Unknown. Assume 24h until measured. | Phase 3 |
| Reserve SoC max value | Assume 100%, verify. | Phase 3 |
| Schedule merge with midnight-spanning slots | v1 doesn't handle correctly. Predbat 30-min slots never span midnight in practice. | Defer until proven needed |
| Multi-device support | Service `device_id` param works in code but UI doesn't expose a picker. Single-device assumption is fine for v1. | Phase 4 if anyone has multiple |

## Hardware bring-up plan

| Phase | Goal | Status |
|---|---|---|
| **1. Mock + skeleton** | Mock server live, HA integration loads, config flow works, sensors populated from mock | ✅ done |
| **2a. Predbat shim** | Shim services callable, idempotency + revert logic, Predbat custom-inverter template | ✅ done |
| **2b. Predbat install** | `nipar44/predbat_addon` Docker container running Predbat, plans against hardcoded test prices, fires shim service calls | ✅ done |
| **2b.1. Shim contract redesign** | Read params from `predbat.best_*` entities (was `sensor.predbat_<inv>_*` — corrected to upstream contract in session 14) instead of service-call args | ✅ done |
| **2c. Octopus prices** | Predbat fetches Agile import + export rates from the public REST API (region L). No account required. | ✅ done |
| **4+. Octopus consumption upgrade** | Install BottlecapDave's HACS integration → real half-hourly consumption sensors replace Riemann `load_today` (better Predbat load forecast). IOG dispatch info also picked up if relevant later. Agile rate path stays on the public URL either way. | ⏸️ account live, gated on Phase 3 verified |
| **3. Hardware reconciliation** | Capture real cloud API traffic via mitmproxy, reconcile mock contract, switch to live endpoint, fix what breaks | ⏸️ install booked 2026-05-19 |
| **4. HACS + tests + CI** | HACS distribution, pytest scaffolding, GitHub Actions | ⏸️ post-Phase 3 |

## Verified flows (Phase 2a, 2026-05-04)

End-to-end smoke tests on <host> against the mock cloud:

1. **`charge_start` →** override TOU written, baseline split correctly around the new slot, operating_mode flipped from `self_consumption` to `time_of_use`, `_predbat_override: true` flag present on the new slot.
2. **`charge_stop` →** baseline restored exactly, operating_mode back to `self_consumption`, no `_predbat_override` slots remaining.
3. **Auto-revert timer →** override applied with end_time ~2 min in future. After end_time elapsed, baseline auto-restored without any further service call. Log message: `revert timer fired — restoring baseline`.

Idempotency check (re-call with same args → no-op) and discharge flows untested on mock; expected to work but not yet exercised.
