## 💷 EP Cube Integration v0.7.0 — Per-Tier Rate Entry on TOU Card

Phase 4.1++ closes the TOU editor's last functional gap: per-tier prices are now editable from the card, not just preserved server-side. The cube's internal tier prices are cosmetic for Predbat users (Predbat optimises against your real Octopus tariff), but for fixed-tariff and non-Predbat users they're load-bearing — and previously the only way to set them was via the EP Cube mobile app.

### ✨ What's new

- **Per-tier rate inputs in the editor card** — three p/kWh inputs per tab (Peak / Mid-peak / Off-peak × Workday / Weekend), inline with each tier's "Add slot" button. Hydrates on first load from a new `tou_prices` extra-state-attribute on `select.ep_cube_operating_mode`. Type a value to override; leave blank to keep the cube's existing per-tier price. Placeholder shows the cube's factory default for empty tiers (`40` / `25` / `5` p/kWh for peak / mid-peak / off-peak).

- **Optional `prices` arg on `ep_cube.set_tou_schedule`** — pass a dict with up to 6 keys (`peak_workday`, `mid_peak_workday`, `off_peak_workday`, `peak_weekend`, `mid_peak_weekend`, `off_peak_weekend`) to override per-tier prices in p/kWh. Range 0-999. Tiers omitted from the dict fall through to preserve-from-cube semantics (existing behaviour). Backwards-compatible — pre-v0.7 callers without `prices` see no behaviour change.

- **`parse_tou_prices()` helper + `tou_prices` attribute** — sibling to `parse_tou_schedule` / `tou_schedule`. Returns the price of the first non-shim slot in each tier, or `None` if the tier is empty (so the card can show a placeholder instead of a misleading "real" value). Automations can read it directly from the select entity's attributes.

### 🐛 Fixed

- **Card hydration race** — the card's first-load guard used to flip `true` as soon as the schedule attribute appeared, even if `tou_prices` wasn't there yet. Result: rate inputs stayed blank forever until the user clicked "Reload from cube" manually. v0.7.0 only sets the guard once prices have successfully landed, so schedule-only ticks keep retrying.

### 🧹 Cleanup

- **Dropped the v0.6.3 legacy synthetic-price tokens** (`0.01` / `0.20` / `1.00`) from the strip set — the one-release migration window has expired. Any pre-v0.6.3 in-flight Predbat overrides have long since rotated out. Slots with these prices on your cube would now look like genuine user slots, which was the whole reason for the v0.6.3 migration in the first place (they collided with realistic fixed-tariff prices and were silently dropping user data from the editor card's hydrated view).

### 📋 Service contract (new field highlighted)

```yaml
service: ep_cube.set_tou_schedule
data:
  peak_workday:     ["16:00-19:00"]
  mid_peak_workday: ["04:30-16:00", "19:00-23:59"]
  off_peak_workday: ["00:30-04:30"]
  peak_weekend:     []
  mid_peak_weekend: []
  off_peak_weekend: []
  switch_to_tou:    false           # optional
  prices:                           # ✨ NEW in v0.7.0, optional
    off_peak_workday: 8.5           # p/kWh
    peak_workday:     28.5
  device_id:        "5613"          # optional
```

Each `prices` key independently routes through: (1) explicit value → (2) cube's existing price → (3) factory default. Pass none of them and behaviour matches pre-v0.7. Pass some of them and only those tiers get overridden.

### 📏 Precision note

The cube's wire format is 2dp on the £-scale = 1p resolution per tier. Sub-p input (e.g. `19.25` p/kWh from an Octopus Agile rate) rounds to `19` on save. Documented in the service description, the card hint text, and `services.py`. For Predbat users this is irrelevant (Predbat optimises against the real Octopus rate, not the cube's internal prices). For fixed-tariff users UK tariffs are usually quoted to whole pence anyway.

### 🧪 Tests

Suite is now **229 tests** (was 200 in v0.6.4):
- 23 new cases in `tests/test_set_tou_schedule.py` covering the `prices` arg (full override, partial preservation, empty-cube-tier with explicit price, schema validation, sub-penny rounding, string coercion, 2-write dance interaction, zero-price accepted) and the new `parse_tou_prices` helper + `_first_non_shim_price` shared scanner.
- 3 hard-coded legacy synthetic prices in `tests/test_shim.py` replaced with `SHIM_PRICE_*` constants.
- 1 new case verifying legacy prices (`0.01` / `0.20` / `1.00`) now flow through as user slots instead of being stripped.

All green on Python 3.12 in CI + local Synology container.

### 📦 Upgrading

- **HACS users**: bump from v0.6.4 → v0.7.0 in the HACS UI.
- **Manual users**: re-copy `custom_components/ep_cube/` + `www/ep-cube-tou-editor.js` from the release zip; bump the card's resource version (Settings → Dashboards → Resources → change `/local/ep-cube-tou-editor.js?v=0.6.4` to `?v=0.7.0`) so browsers re-fetch.
- **HA restart required** — Python-side service + entity changes.

### 🛣 What's next

- **v0.7.x** — placeholder defaults driven by `predbat.metric_low_rate_threshold` / `high_rate_threshold` (or the current Octopus rate via the BottlecapDave integration) instead of static constants. Chipped during v0.7.0 testing.
- **v0.7.x** — `queryDataElectricityV2` cloud-stats expansion (signed grid import/export, `*_yesterday` variants, lifetime totals with `RestoreSensor` seeding) — waiting on a mitmproxy capture session.
- **v0.8** — auto-paint TOU schedule from Predbat's rate thresholds + Agile forecast (one-click "match Predbat's bucketing").

### ☕ Support

If this saves you the mitmproxy sessions and Python hours it took to build, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
