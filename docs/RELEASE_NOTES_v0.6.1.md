## 🩹 EP Cube Integration v0.6.1 — TOU Editor Polish + CI Maintenance

Small follow-up to [v0.6.0](https://github.com/SkiLtY/ha-ep-cube/releases/tag/v0.6.0). Two UX papercuts from the TOU editor card surfaced during the v0.6.0 real-cube test, plus a CI maintenance bump for GitHub's [2026-06-02 Node 20 deprecation](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/).

### ✨ What's new

- **Smarter slot defaults in the TOU editor card** — adding a new slot used to default to `00:00-00:00`. If you then edited only the end time, you got `00:00-23:30`, which clobbered the start of any earlier morning slot and tripped the overlap validator on first save. New defaults tail the latest end-time across all tiers on the active profile (1-hour default duration, capped at `23:59`), so a freshly-added slot lands in unclaimed territory and edits become additive rather than destructive.
- **Copy workday ↔ weekend** — added a small *Copy from workday / Copy from weekend* link under the tab bar. One-click mirror; prompts before overwriting if the destination tab has any slots. Most useful for the common case of weekend = workday with one or two tweaks.

### 🔧 Maintenance

- **GitHub Actions Node 24 bump** — `actions/checkout@v4` → `@v5`, `actions/setup-python@v5` → `@v6`, `softprops/action-gh-release@v2` → `@v3`. All three Action versions now run on Node 24, ahead of GitHub's 2026-06-02 deadline when Node 20 stops being available on hosted runners.

### 📦 Upgrading

- **HACS users**: bump from v0.6.0 → v0.6.1 in the HACS UI (no breaking changes).
- **Manual users**: re-copy `custom_components/ep_cube/` from the release zip; re-copy `www/ep-cube-tou-editor.js` if you have the editor card installed (then browser hard-refresh to bust the resource cache).
- **No HA restart needed for the card change alone** — the only Python change is the manifest version bump.

### 🛣 What's next

- **v0.7** — `queryDataElectricityV2` cloud-stats expansion (signed grid import/export, `*_yesterday` variants, lifetime totals with `RestoreSensor` seeding)
- **v0.8** — auto-paint TOU schedule from Predbat's rate thresholds + Agile forecast (one-click "match Predbat's green/amber/red classification")
- **v1.0** — HACS Default listing + multi-user validation across regions

### ☕ Support

If this saves you the mitmproxy sessions and Python hours it took to build, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
