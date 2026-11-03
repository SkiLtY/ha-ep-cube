## 🏷️ EP Cube Integration v1.7.0 — force export and force charge without Predbat

> **TL;DR** — two new pairs of services let you export to the grid or charge from it for a set time from an automation, a script or Developer Tools, with the same safe revert Predbat's windows use. They step aside while Predbat is in charge.

### ✨ New

- **`ep_cube.force_export`** — export battery energy to the grid for `duration_minutes` (1–240), stopping at `reserve_pct`. It never goes below the cube's own self-consumption reserve. It uses the cube's Dynamic tariff mode (set up in the EP Cube app), whatever your *Force export mode* option says, and the cube returns to its previous mode at the end. `ep_cube.force_export_stop` ends it early.
- **`ep_cube.force_charge`** — charge from the grid for `duration_minutes` (1–240), stopping at `target_pct`. It is carried out the way your *Force charge mode* option says, as a Predbat charge window is: with Dynamic tariff it stops at the target; with the time-of-use off-peak option there is no target, so it charges until the time is up. `ep_cube.force_charge_stop` ends it early.
- **Set the Dynamic prices from Home Assistant.** `number.ep_cube_set_dynamic_buy_below_price` and `number.ep_cube_set_dynamic_sell_above_price` (p/kWh, 0 to 500) write the Dynamic tariff's two prices, for people who set them by hand instead of through Predbat. They are available only in Dynamic tariff mode and not while a forced export or charge is running, a change is refused while Predbat is controlling the cube (like the force services), and a change is remembered as the cube's setting for later reverts. A buy-below of 0 with a sell-above of 0 to 5 is refused, because it is exactly what a forced export writes. The range is wide enough to force an import or export by hand (sell-above 0 with a buy-below above 0 to export, both at 500 to import), with no timer or revert, so set them back afterwards.
- **Lifetime energy totals for the Energy dashboard.** `grid_import_total`, `grid_export_total`, `solar_total` and `backup_total` (kWh, state class `total_increasing`) read the cloud's lifetime figures, so after a restart they carry on at the cloud's value instead of starting from zero. They never step backwards: a short cloud glitch (a zero or a lower reading) is ignored, so the dashboard cannot count a false reset. Until the cloud's first year roll-over (January) the lifetime figure equals the year's.
- **Safe by design.** Both reuse the machinery Predbat's windows already run on: the baseline snapshot, the two-step revert, the watchdog that checks the cube left Dynamic mode, the missing-price notification and the five-minute "is it really charging" check. A failed write puts the cube back and reports an error. Calling a start service again begins a fresh window.
- **Predbat stays in charge.** The two start services run only when it is safe to say Predbat is not driving the cube. They refuse, with a clear message, whenever Predbat's `switch.predbat_set_read_only` reads `off`, `unavailable` or `unknown`, because Predbat's next plan would replace the action. They also refuse if that switch is missing but was seen in the last 24 hours, which is the short gap after a Home Assistant or Predbat restart before Predbat republishes it (the integration remembers when it last saw the switch). They run when the switch reads `on` (Predbat's read-only, monitor mode) or when there is no sign of Predbat at all. The stop services always work, so a running override can be ended at any time.

- **Example: fixed-hours export.** `examples/ha_config/packages/ep_cube_fixed_export.yaml` is a ready-made package for people without Predbat: five helpers (on/off, start, end, minimum battery level, reserve level) and an automation that calls `ep_cube.force_export`, for example 17:00 to 19:00 while the battery is above 40%. Copy it next to your other packages and restart.

### ⚠️ Changed

- **`ep_cube.debug_dynamic_export` is removed.** `ep_cube.force_export` replaces it. The only thing it had that the new service does not is the `sell_above` test override. `ep_cube.debug_dynamic_charge` is unchanged.

### 📦 Upgrading

Update via HACS and restart Home Assistant. If an automation called `ep_cube.debug_dynamic_export`, switch it to `ep_cube.force_export` and give it a `reserve_pct`.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
