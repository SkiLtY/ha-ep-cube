## 🏷️ EP Cube Integration v1.7.0 — force export and force charge without Predbat

> **TL;DR** — two new pairs of services let you export to the grid or charge from it for a set time from an automation, a script or Developer Tools, with the same safe revert Predbat's windows use. They step aside while Predbat is in charge.

### ✨ New

- **`ep_cube.force_export`** — export battery energy to the grid for `duration_minutes` (1–240), stopping at `reserve_pct`. It never goes below the cube's own self-consumption reserve. It uses the cube's Dynamic tariff mode (set up in the EP Cube app), whatever your *Force export mode* option says, and the cube returns to its previous mode at the end. `ep_cube.force_export_stop` ends it early.
- **`ep_cube.force_charge`** — charge from the grid for `duration_minutes` (1–240), stopping at `target_pct`. It is carried out the way your *Force charge mode* option says, as a Predbat charge window is: with Dynamic tariff it stops at the target; with the time-of-use off-peak option there is no target, so it charges until the time is up. `ep_cube.force_charge_stop` ends it early.
- **Safe by design.** Both reuse the machinery Predbat's windows already run on: the baseline snapshot, the two-step revert, the watchdog that checks the cube left Dynamic mode, the missing-price notification and the five-minute "is it really charging" check. A failed write puts the cube back and reports an error. Calling a start service again begins a fresh window.
- **Predbat stays in charge.** The two start services run only when it is safe to say Predbat is not driving the cube. They refuse, with a clear message, whenever Predbat's `switch.predbat_set_read_only` reads `off`, `unavailable` or `unknown`, because Predbat's next plan would replace the action. They also refuse if that switch is missing but was seen in the last 24 hours, which is the short gap after a Home Assistant or Predbat restart before Predbat republishes it (the integration remembers when it last saw the switch). They run when the switch reads `on` (Predbat's read-only, monitor mode) or when there is no sign of Predbat at all. The stop services always work, so a running override can be ended at any time.

### ⚠️ Changed

- **`ep_cube.debug_dynamic_export` is removed.** `ep_cube.force_export` replaces it. The only thing it had that the new service does not is the `sell_above` test override. `ep_cube.debug_dynamic_charge` is unchanged.

### 📦 Upgrading

Update via HACS and restart Home Assistant. If an automation called `ep_cube.debug_dynamic_export`, switch it to `ep_cube.force_export` and give it a `reserve_pct`.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
