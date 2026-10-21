## 🔋 EP Cube Integration v1.6.2 — battery cycles and the warranty end date

> **TL;DR** — three new diagnostic sensors let you watch your battery against its warranty: lifetime energy discharged, estimated full cycles, and the warranty end date straight from the cloud ([#18](https://github.com/SkiLtY/ha-ep-cube/issues/18)). Nothing to configure.

### ✨ New

- **Battery discharged (lifetime)** and **Battery cycles (estimated)** (diagnostics). They are saved across restarts, and on first start they pick up your existing history from the recorder. The cycles sensor also shows the share of the 6,000-cycle allowance, your average cycles a day, and the daily budget for your warranty. These are estimates from battery state-of-charge changes, so they may differ slightly from EP Cube's own count.
- **Warranty end date** (diagnostic), read from the cloud, so a 10-year and a 15-year warranty both show correctly. It is unavailable if the cloud doesn't return it.
- The sample dashboard gains a **Battery health** card on the Control tab.

### 📦 Upgrading

Update via HACS and restart Home Assistant. The new sensors appear by themselves; the lifetime figure fills in from your history within a minute or two.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
