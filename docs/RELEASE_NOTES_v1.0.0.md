## 🪜 EP Cube Integration v1.0.0 — Opinionated Predbat Bridge (Breaking Change)

> **TL;DR** — the TOU editor card and `ep_cube.set_tou_schedule` service are gone. From v1.0 the integration treats Predbat as the single source of truth for time-of-use control. If your cube has user-painted TOU slots while Predbat is running, an HA Repair issue offers a one-click wipe. If you want manual TOU control without Predbat, use the EP Cube mobile app or [Bobsilvio/epcube](https://github.com/Bobsilvio/epcube).

### Why this is breaking

The TOU editor existed as a relief valve for "Predbat isn't doing what I want right now." But overriding at the cube creates state divergence: Predbat re-plans against its model of the cube while the cube is actually running a hand-painted schedule the model doesn't know about. The right fix for "Predbat isn't doing what I want" is to **tune Predbat** (`metric10_weight`, `metric_battery_value_scaling`, etc.) — not to override at the device.

The honest product framing for v1.0:

- **Predbat + variable tariff users** (the integration's primary audience): everything they need is in the shim's `charge_start` / `discharge_start` / `charge_freeze` / `idle` services. They never needed `set_tou_schedule` and shouldn't be tempted by it.
- **Fixed-tariff non-Predbat users** (the audience the editor was secretly built for): better served by Bobsilvio's `bobsilvio/epcube` HACS integration, or just the EP Cube mobile app. Neither has an "is Predbat tuned right?" problem to fight.

### 💥 Breaking changes

- **Removed `ep_cube.set_tou_schedule` service.** Any automation calling it will fail. Migrate to one of:
  - **EP Cube mobile app** for ad-hoc manual TOU painting.
  - **Bobsilvio's [epcube](https://github.com/Bobsilvio/epcube) integration** if you want manual TOU as a permanent HA-native control surface (deliberately different design from this one).
- **Removed `www/ep-cube-tou-editor.js`** (the Lovelace editor card). If you have it installed:
  - Settings → Dashboards → Resources → delete the `/local/ep-cube-tou-editor.js` entry.
  - The `dashboards/ep_cube.yaml` no longer references it. If you customised the dashboard, the `type: custom:ep-cube-tou-editor` card block can be removed.
- **Removed `tou_schedule` and `tou_prices` extra-state-attributes** from `select.ep_cube_operating_mode`. If you read these from automations, switch to the EP Cube app or a different integration for the underlying data.
- **`parse_tou_schedule()` and `parse_tou_prices()` helpers removed** from `services.py`. They were public-but-internal — listed here for anyone who imported them in a custom module.

### ✨ New: HA Repair flow for the Predbat-priority transition

When the coordinator detects:

1. The cube has at least one **non-shim** slot in any of the six non-DST tier lists (workday + weekend × peak / mid-peak / off-peak), AND
2. At least one entity in the `predbat.*` domain exists on this HA instance,

…an HA Repair issue appears (Settings → System → Repairs) titled **"Manual TOU slots on the cube while Predbat is running."** Selecting **Submit** wipes the six non-DST tier lists on the cube in a single guided flow:

- Abandons any in-flight Predbat shim override first (user-wins).
- Reads current state, strips stale shim-signature slots, builds an overrides dict that empties the six tier lists.
- Uses the **2-write dance** when the cube is in non-TOU mode (write A flips to TOU and lands the empty lists; write B flips back to the original mode). Same provenance and quirks as the shim's existing dance — see [services.py](custom_components/ep_cube/services.py) `PredbatShim`.
- Triggers a coordinator refresh — the detection helper auto-clears the issue once it sees the wiped snapshot.

**DST tier lists are deliberately left intact.** They're shared-across-year state; the conflict surface lives entirely in the six non-DST lists.

The repair flow is **idempotent** and **non-destructive without confirmation** — the issue surfaces, the user reads, the user clicks. No auto-wipe.

### 🧹 What was cleaned up

- `custom_components/ep_cube/services.py` lost ~400 lines: `handle_set_tou_schedule` + `SET_TOU_SCHEDULE_SCHEMA` + `_PRICES_SCHEMA` + `_USER_SLOT_RE` + `_parse_user_slot` + `_validate_day_profile` + `_user_slot_to_wire` + `_slot_wire_to_user` + `_existing_house_price` + `_DEFAULT_PRICE_BY_USER_FIELD` + `parse_tou_schedule` + `parse_tou_prices`.
- `tests/test_set_tou_schedule.py` removed (~900 lines, 30+ cases). The 2-write dance is still under test coverage via `tests/test_shim.py` (the shim uses the same pattern for charge / discharge / freeze).
- `const.DEFAULT_TIER_PRICE_*` removed (only `set_tou_schedule` referenced them).
- `services.yaml` lost the `set_tou_schedule` entry; the shim services remain.

### 📦 What's kept

- Predbat shim: `charge_start`, `charge_stop`, `discharge_start`, `discharge_stop`, `charge_freeze`, `discharge_freeze`, `idle` — unchanged.
- `debug_freeze` diagnostic service — unchanged.
- All 22 sensors, 1 select (operating mode), 2 numbers (reserve SoCs), 2 switches (grid-charge allow + DST) — unchanged.
- `select.ep_cube_predbat_inverter_mode` + `number.ep_cube_predbat_charge_limit` (Predbat-entity-first stubs from v0.7 line) — unchanged.
- `parse_tou_schedule` callers in dashboards / cards — none in the public repo; if you forked the editor card it'll stop working without the helpers.

### 🧪 Tests

Suite shrinks from 229 → ~200 with the deletion. The repair flow itself gets light coverage in this release; the cube-write path is exercised by the existing shim tests. Suite is green on Python 3.12.

### 📦 Upgrading

- **HACS users**: bump v0.7.0 → v1.0.0 in the HACS UI. **Restart HA.** After restart, if the cube has manual TOU slots and you run Predbat, an HA Repair will appear — click through to wipe them.
- **Manual users**: re-copy `custom_components/ep_cube/` from the release zip. If you'd installed `www/ep-cube-tou-editor.js`, remove it from `/config/www/` and remove the Lovelace resource entry. Restart HA.
- **Automation cleanup**: search your `automations.yaml` and node-red flows for `ep_cube.set_tou_schedule` — these calls now fail. Decide per-call: drop entirely (let Predbat handle it), move to the EP Cube app, or migrate to Bobsilvio's integration.

### 🛣 What's next

- **v1.0.x** — `queryDataElectricityV2` cloud-stats expansion (signed grid import/export, `*_yesterday` variants, lifetime totals with `RestoreSensor` seeding) — waiting on a mitmproxy capture session.
- **v1.1** — HACS Default submission.
- **v1.x** — [HomeAssistant-OctopusEnergy](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy) — half-hourly smart-meter consumption replaces Riemann `load_today`. Gated on Octopus Home Mini arrival.

### Why v1.0 not v0.8

This is the first release whose **scope** matches the original product statement: "let Predbat control the EP Cube." Earlier versions stopped short of being opinionated about it and shipped escape hatches that, in practice, made the Predbat path harder to reason about. v1.0 deletes the escape hatches and replaces them with a one-click handoff.

### ☕ Support

If this saves you the mitmproxy sessions and Python hours it took to build, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
