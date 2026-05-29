## 🔄 EP Cube Integration v0.6.2 — TOU Editor Card Hydrates From Cube State

Closes the [v0.6.0](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v0.6.0) deferral that left the TOU schedule editor card showing only user-typed values — never the cube's actual current schedule. The card now reflects real state on mount, after every save, and on-demand via a new *Reload from cube* button.

### ✨ What's new

- **Card hydration from cube state** — the editor card now reads the cube's current workday + weekend tier lists from a new `tou_schedule` attribute on the `select.ep_cube_operating_mode` entity. Auto-hydrates once on first mount (so it can't clobber in-progress edits on every poll tick), re-hydrates after a successful save (so the post-save view reflects what actually landed on the cube), and exposes a *Reload from cube* link under the tab bar for on-demand refresh.
- **Coordinator polls `getSwitchMode`** — added alongside the existing `homeDeviceInfo` poll. Failure of the schedule fetch logs at DEBUG and keeps the last cached value, so it can't take down the critical sensor data path. Cube reads remain cheap; no rate-limit concern observed.
- **`select.ep_cube_operating_mode` exposes `tou_schedule` attribute** — nested shape `{workday: {peak: [...], mid_peak: [...], off_peak: [...]}, weekend: {...}}` with shim-signature slots stripped (so in-flight Predbat overrides don't leak into the user's view) and wire-format prices removed (user format `HH:MM-HH:MM`, never `HH:MM_HH:MM_PRICE`).
- **`ep_cube.set_tou_schedule` triggers coordinator refresh on success** — so the card's auto-re-hydration after save sees the cube's now-current state, not the 30s-stale view.

### 🐛 Fixed

- **"Did my save actually land?" ambiguity** — previously the only way to confirm a save was to open the EP Cube mobile app's schedule tab or grep HA logs for `set_tou_schedule applied`. Now the card itself is the source of truth: open it and see what's on the cube.
- **Weekend-wipe footgun** — if you painted weekend slots, then later edited only workday and saved, the empty weekend tab would silently wipe the cube's weekend schedule. With hydration on mount, the weekend tab starts populated from cube state so accidental clears can't happen unless you explicitly empty it.

### 🧪 Tests

Six new pytest cases at `tests/test_set_tou_schedule.py::TestParseTouSchedule` covering empty state, wire→user format conversion, weekend list separation, shim-signature stripping, malformed slot tolerance, and DST list isolation. Suite is now 178 tests.

### 📦 Upgrading

- **HACS users**: bump from v0.6.1 → v0.6.2 in the HACS UI.
- **Manual users**: re-copy `custom_components/ep_cube/` from the release zip; re-copy `www/ep-cube-tou-editor.js` if you have the editor card installed (then browser hard-refresh to bust the resource cache).
- **HA restart required** — coordinator + select changes are Python-side. The card hydration only works against the new backend, so restart HA after the integration update before refreshing the browser.

### 🛣 What's next

- **v0.7** — per-tier rate entry on the editor card (chipped, ready to start)
- **v0.7** — `queryDataElectricityV2` cloud-stats expansion (signed grid import/export, `*_yesterday` variants, lifetime totals with `RestoreSensor` seeding)
- **v0.8** — auto-paint TOU schedule from Predbat's low-rate threshold + Agile forecast (single off-peak tier; mid-peak + peak default to fallback)
- **v1.0** — HACS Default listing + multi-user validation across regions

### ☕ Support

If this saves you the mitmproxy sessions and Python hours it took to build, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
