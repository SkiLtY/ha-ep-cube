## 🕐 EP Cube Integration v0.6.0 — TOU Schedule Editor

Phase 4.1 lands: a user-facing way to edit the cube's Time-of-Use schedule from Home Assistant, end-to-end. New `ep_cube.set_tou_schedule` service plus a bundled Lovelace editor card with workday + weekend tabs, inline validation, and a one-click "Switch to TOU mode when saving" flow. Verified against the live cube on 2026-05-29 with full round-trip confirmation in the EP Cube mobile app.

### ✨ What's new

- **`ep_cube.set_tou_schedule` service** — replaces the cube's workday + weekend non-DST tier lists in one atomic write. DST tier lists, reserves, day masks and per-tier prices are preserved server-side from the cube's current state (read-modify-write under the hood). Slots are validated for format, within-tier overlap, and cross-tier overlap before any cloud call. If a Predbat shim override is in flight, it's abandoned (user-wins) so your edit takes immediate effect.
- **Bundled Lovelace editor card** — `www/ep-cube-tou-editor.js`, a Lit-based custom card with two tabs (Workday / Weekend), three tier editors per tab (Peak / Mid-peak / Off-peak), native HTML5 time pickers, +/- slot controls, inline validation mirroring the backend rules, and a "Switch the cube into Time-of-Use mode when saving" checkbox for configure-then-activate flows. No external dependencies — uses Lit primitives bundled with HA's frontend.
- **Dashboard integration** — `dashboards/ep_cube.yaml` adds the editor as an always-visible top-level card (deliberately *not* gated by current operating mode, so users can paint their schedule before switching to TOU).
- **22 new pytest cases** — validation matrix, price preservation, DST round-trip, stale-shim-slot stripping, switch-to-TOU flag, shim coexistence, validation-before-cloud-IO guarantee. Suite is now 172 tests.

### 📦 Card installation

The editor card is **optional** — only needed if you want a UI for editing TOU slots. Service-only users (e.g. driving it from automations) can skip this.

1. Copy `www/ep-cube-tou-editor.js` from this repo to your HA config's `www/` directory (so the path is `/config/www/ep-cube-tou-editor.js`).
2. *Settings → Dashboards → Resources → ⊕ Add Resource* → URL `/local/ep-cube-tou-editor.js`, type *JavaScript module* → Create.
3. Restart your browser (hard refresh) so HA picks up the new module.
4. Re-paste [`dashboards/ep_cube.yaml`](https://github.com/SkiLtY/ha-ep-cube/blob/main/dashboards/ep_cube.yaml) into your dashboard's Raw configuration editor (the editor card is now baked in below the operating-mode picker).

For a fully-baked install with no copying, [docs/DEPLOY_SYNOLOGY.md](https://github.com/SkiLtY/ha-ep-cube/blob/main/docs/DEPLOY_SYNOLOGY.md) shows how to bind-mount the card directly via `docker-compose.override.yml` so future `git pull`s auto-flow without a re-copy step.

### 📋 Service contract

```yaml
service: ep_cube.set_tou_schedule
data:
  peak_workday:     ["16:00-19:00"]
  mid_peak_workday: ["04:30-16:00", "19:00-23:59"]
  off_peak_workday: ["00:30-04:30"]
  peak_weekend:     []
  mid_peak_weekend: []
  off_peak_weekend: []
  switch_to_tou:    false   # optional, default false
  device_id:        "5613"  # optional, only needed if multiple cubes
```

Slot format: `HH:MM-HH:MM` (24-hour). Slots can't cross midnight — use `23:59` to end a slot at the end of the day (matches the EP Cube mobile app's convention; the cube's wire format rejects `end <= start`).

### ⚠️ Known limitations

- **Midnight-crossing slots not supported** — convention is to end slots at `23:59` (see above). v0.7+ may add auto-split for true midnight-crossing windows if there's demand.
- **Only the non-DST tier lists are surfaced in the card** — the cube also stores separate DST tier lists for use during summer time. The service preserves them untouched on write, but the card doesn't expose them yet. If you set DST tier lists via the EP Cube mobile app, they survive `set_tou_schedule` calls intact.
- **Per-tier prices preserved, not editable from the card** — the service uses the cube's existing per-tier price for new slots (or sensible defaults if the tier is currently empty: 0.40 peak / 0.25 mid / 0.05 off). The cube's TOU prices are internal tier-priority labels, not real tariff prices, so this is mostly a non-issue for Predbat-driven setups.

### 🛣 What's next

- **v0.6.1** — TOU editor UX polish (smarter slot defaults, weekday → weekend copy helper)
- **v0.7** — `queryDataElectricityV2` cloud-stats expansion (signed grid import/export, `*_yesterday` variants, lifetime totals with `RestoreSensor` seeding)
- **v0.8** — auto-paint TOU schedule from Predbat's rate thresholds + Agile forecast (one-click "match Predbat's green/amber/red classification")
- **v1.0** — HACS Default listing + multi-user validation across regions

### ☕ Support

If this saves you the mitmproxy sessions and Python hours it took to build, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
