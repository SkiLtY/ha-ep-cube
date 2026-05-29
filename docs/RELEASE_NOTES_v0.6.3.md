## 🎯 EP Cube Integration v0.6.3 — Shim Price Refactor

Small but important fix that closes a real footgun: the synthetic prices the Predbat shim used to tag its temporary override slots (`0.01` / `0.20` / `1.00`) collided with realistic UK tariff prices. A user on a fixed-rate tariff with `1.00` p/kWh peak slots would have those slots **silently stripped** from the TOU editor card's hydrated view (introduced in v0.6.2). Surfaced during a live `debug_freeze` test on 2026-05-29.

### ✨ What's new

- **Shim synthetic prices migrated to `2.22` / `3.33` / `4.44`** — all above Agile's 100p/kWh daily cap, so no realistic UK retail tariff hits them. The repeating-digit pattern also makes shim slots visually identifiable if a user ever spots one in the EP Cube mobile app's schedule view.
- **One-release migration window** — `_SHIM_PRICE_TOKENS` keeps the legacy values (`0.01` / `0.20` / `1.00`) in its match set so any leftover shim slots from a pre-v0.6.3 Predbat run still get stripped cleanly on next read. Legacy entries will be dropped in v0.7 once any in-flight overrides have rotated out of cube memory.
- **One new pytest case** verifying both new + legacy synthetic prices are stripped (suite now 179 tests).

### 🐛 Fixed

- **Editor card hiding genuine user slots at `1.00` p/kWh** — fixed-tariff users with realistic peak prices at the £1 cap would see those slots vanish from the hydrated card view. Migration window catches both old and new synthetic prices, so any leftover shim slots from prior Predbat runs are still cleaned up.

### 📦 Upgrading

- **HACS users**: bump from v0.6.2 → v0.6.3 in the HACS UI.
- **Manual users**: re-copy `custom_components/ep_cube/` from the release zip.
- **HA restart required** — Python-side constants change.
- **No card change** — `www/ep-cube-tou-editor.js` unchanged in this release.

### 📋 User-facing constraint update

Previously: "don't manually configure slots at `0.01` / `0.20` / `1.00`."

Now: "don't manually configure slots with prices in the synthetic set." The synthetic set is currently `{0.01, 0.20, 1.00, 2.22, 3.33, 4.44}` during the v0.6.3 migration window; will narrow to `{2.22, 3.33, 4.44}` in v0.7.

In practice this matters only for fixed-tariff users (Predbat / Agile users won't manually set these prices since the cube's TOU prices are internal tier-priority labels for their setup).

### 🛣 What's next

- **v0.7** — per-tier rate entry on the editor card (chipped); drop legacy shim-price tokens from migration window.
- **v0.7** — `queryDataElectricityV2` cloud-stats expansion.
- **v0.8** — auto-paint TOU schedule from Predbat's low-rate threshold + Agile forecast.

### ☕ Support

If this saves you the mitmproxy sessions and Python hours it took to build, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
