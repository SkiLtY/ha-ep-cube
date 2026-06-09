## 🪜 EP Cube Integration v1.1.2 — Dashboard Fix + Backup-Loads Clarification

> **TL;DR** — small patch on top of v1.1.1, surfaced by the HACS pre-submission smoke test. One broken entity row in the bundled dashboard YAML, one README clarification about what `backup_today` actually measures. No code change, no behaviour change. Upgrade if you use the bundled `dashboards/ep_cube.yaml` or have ever wondered what "Backup loads today" means on your install.

### 🐛 What's fixed

**`dashboards/ep_cube.yaml` — "Energy today" card showed "Entity not found" on the Self-consumption row.** Referenced `sensor.ep_cube_self_consumption_pct` (matching the integration's `translation_key`) but HA derives entity-IDs from the entity's *display name*, not the translation key — so the actual ID is `sensor.ep_cube_self_consumption`. README already documents this slug-derivation gotcha for users editing dashboards; the bundled YAML was the one place that didn't follow its own advice.

Bug was present from v1.1.0 (when the Energy today card was first added). Silent on existing installs unless you scrolled past the row.

### 📝 What's documented

**README — backup-loads semantics callout.** New `> [!NOTE]` admonition under the Sensors table explaining that `backup_today` / `backup_yesterday` count kWh delivered through the cube's backup-output terminal, *not* "loads that stayed up during an outage". Whether this reads as whole-house or essential-circuits-only depends entirely on what your installer wired through the backup panel. Adds context that outage-resilience under UK G99/G100 regs needs an external EPS Gateway — the cube physically refuses to supply via the backup terminal during a grid outage without one, even though it continues to meter kWh through it under normal operation.

### 🧪 HACS pre-submission smoke test

This release was triggered by the HACS Custom Repository install-path verification on a throwaway HA instance. Findings:

- ✅ HACS install via official one-liner: clean
- ✅ Custom Repository → Integration download: clean
- ✅ Config flow against live cube (multi-region + captcha + bearer): clean
- ✅ All 33 entities (26 sensors + 5 controls + 2 Predbat stubs) populated with sane live data
- ✅ Logs clean, Repairs clean (Predbat-priority detector correctly silent without a Predbat container)
- ✅ Brand icon rendering on device detail page (HA core's `/api/brands/integration/ep_cube/icon.png` serving from `custom_components/ep_cube/brand/`)
- ⚠️ HACS store list shows "icon not available" — HACS fetches store icons direct from `brands.home-assistant.io` CDN, bypassing HA's brands endpoint. Not fixable from the integration side; resolves itself once HACS Default is accepted and we re-submit to `home-assistant/brands` (post-submission).
- 🐛 Dashboard YAML self-consumption row → fixed above

### 📦 Upgrading

- **HACS users**: bump v1.1.1 → v1.1.2. If you've pasted the dashboard YAML, re-open it via Raw Configuration Editor and re-paste from [`dashboards/ep_cube.yaml`](https://github.com/SkiLtY/ha-ep-cube/blob/main/dashboards/ep_cube.yaml) (or hand-edit the one line). Otherwise nothing to do.
- **Manual users**: re-copy `custom_components/ep_cube/` from the release zip if you want the version bump in HA's UI. No functional difference vs v1.1.1 in the integration code itself.

### 🛣 What's next

- **HACS Default submission** — smoke test passed (this release is the "fix the hiccups" pass), submitting next.
- **v1.2** — cube-native monthly + annual rollups (Phase 4.2 Tier 3) + dashboard KPI tile refresh.
- **v1.3** — lifetime totals (Phase 4.2 Tier 4) + eco metrics (Tier 5).

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
