## 🏷️ EP Cube Integration v1.7.1 — a charge freeze no longer outlasts the charge

> **TL;DR** — if Predbat's "freeze charge" fired after a charge had finished, the cube could sit on a raised reserve for hours while the house ran from the grid. The integration now holds the battery only while the plan really has a charge running, and only until it ends.

### 🐛 Fixed

- **A freeze held on far too long.** When the battery reaches its target inside a charge window, Predbat asks the integration to hold it there (`ep_cube.charge_freeze`). The integration raised the Self-Consumption reserve to the current charge and kept it there until the end of the window Predbat had published. That window can be a leftover from an earlier planning pass: on 10 Nov it said 01:00 to 13:30 while the plan charged only until 05:30, so the reserve stayed at 100% for about seven hours and the house drew from the grid instead of the battery. A freeze now only starts while Predbat's own plan has a charge running, and it ends when that charge ends. If there is no charge in the plan, or the plan cannot be read, the call is ignored and the cube stays in Self-Consumption.

### 📦 Upgrading

Update via HACS and restart Home Assistant. No settings to change.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
