## 🏷️ EP Cube Integration v1.8.2 — Retry a failed revert at the end of a window

> **TL;DR** — if the cloud has a hiccup just as a forced charge or export window ends, the integration now keeps trying to put the cube back, instead of giving up. Nothing else changes.

### 🐛 Fixed

- **A failed stop now retries.** When a window ends, the integration restores the cube's normal mode. If that write failed (for example the cloud saying the cube was offline for a moment), the end-of-window timer already retried a few times, but the `charge_stop` and `discharge_stop` services did not. They raised the error and left the cube on the override until the next check caught it. They now arm the same bounded retry before reporting the error, so a brief cloud outage no longer leaves the cube stuck.
- The retry is limited, so it doesn't hammer the cloud. If it still fails after the last attempt, the log says so and asks you to check the cube's mode.

### ⚠️ Worth knowing

- Seen once on a real cube (21 Nov, "Device is offline" at the end of an export), and the cube had recovered on its own within a minute. This change is a safety net, not a fix for a cube that stays stuck.

### 📦 Upgrading

Update via HACS and restart Home Assistant. No settings change.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
