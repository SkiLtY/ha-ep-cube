## 🏷️ EP Cube Integration v1.6.8 — fewer cloud writes during a Dynamic export or charge

> **TL;DR** — when Predbat's target moves by a point or two while an export or charge is running, the integration now leaves the cube alone instead of re-sending the whole setting each time.

### ✨ Improved

- **No rewrite for a one-point wobble.** Predbat re-plans every few minutes, and its target level often moves by a point or two. A Dynamic export or charge was re-applied every time it did, for example five times in 45 minutes as the target moved between 32% and 34%. Each of those is a write to the EP Cube cloud, and the cloud sometimes answers with an error. A running export or charge whose stopping level is within 2 points of the new target is now left as it is. The difference is measured from the level last written, so a steady drift still gets through, and a new end time is still applied.

### 📦 Upgrading

Update via HACS and restart Home Assistant.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
