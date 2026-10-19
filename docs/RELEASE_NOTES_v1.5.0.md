## ⚡ EP Cube Integration v1.5.0 — EV charger sensors, and a dashboard that reconciles with your bill

> **TL;DR** — The dashboard gains a Month tab with a daily billed-cost chart that matches Octopus's own page, the Week tab stops showing wrong self-sufficiency figures, and the example weekly package now adds up finished days instead of using a utility meter that over-counted.
>
> EV charger sensors (and a house load that includes the car), contributed by [Eric Fisher (@eafisher11)](https://github.com/eafisher11/ha-ep-cube), who built and tested them on a US cube. Thank you, Eric!

### ✨ What's new

- **EV charger power and EV charger today.** Two new sensors, added only when the cube reports an EV charger (`hasEv`, or `hasEvse` on the EU payload), so installs without a charger don't get two permanently-zero entities. *EV charger today* is the cube's own daily total and can go in the Energy dashboard as an individual device.
- **The house load now includes EV charging.** The EV is reported on its own channel, outside `backUpPower` and `nonBackUpPower`: on a real US payload `backUpPower` 77 + `evPower` 162 = `smartHomePower` 239. Leaving it out under-reported the load and made the derived battery power look too low while a car charged. Installs without a charger report 0 there, so nothing changes for them.
- **A Month tab on the dashboard.** Month-to-date energy and cost, and a chart of Octopus's billed cost for every day of the calendar month: bought as bars above zero (with the standing charge as the base, behind a "Show standing charge" checkbox), sold below zero, and a net line with a red or green dot for spent or earned. The figures match Octopus's own dashboard.
- **Estimates until Octopus bills a day.** Octopus publishes each day's figure the evening after, so the Week, Yesterday and Today tabs now show Predbat's estimate (marked "est.") until then, and the billed figure takes over once it is published. Today also has an *End of day (estimate)* tile (what has been spent so far plus Predbat's plan to midnight), with the split underneath and a line you can hold against your smart meter (imports + standing charge, exports, net).
- **A `CONTRIBUTING.md`.** How to help, how to send a pull request, and how contributions are licensed (MIT, authorship kept and credited).

### 🔧 Fixed

- **Self-sufficiency no longer goes negative.** The cube's own percentages (and my first Week-tab formula) count grid import that charges the battery as unmet load, so a day with a cheap overnight charge could read 0% or -137%. All four tabs now show *Solar used at home* and *Solar covers consumption*, both between 0 and 100%.
- **The weekly package no longer over-counts.** The example `ep_cube_weekly.yaml` used `utility_meter`s on the `*_today` sensors. They treat every dip in a cloud counter as a reset, which inflated "consumed" by about 15% in one month, and its billed-cost meters dropped every day cheaper than the day before. It now adds each finished day once (guarded so restarts can't double-count), keeps import with and without the standing charge, and adds the unbilled day's standing charge so the totals equal Octopus's own page.
- **The Month chart tooltip** looks the day up by date, so hovering one of the coloured dots no longer mixes figures from different days.

### ⚠️ Worth knowing

- **The dashboard and the example package are not part of the HACS download** (HACS installs `custom_components/ep_cube`). Copy `dashboards/ep_cube.yaml` and `examples/ha_config/packages/ep_cube_weekly.yaml` from this repository as before.
- **If you already use the weekly package, replace it.** The new file needs your Octopus meter serial and MPAN in two places (replace the `<...>` parts), defines new helpers, and removes the old weekly/monthly utility meters. After a restart Home Assistant may list Repairs warnings about the old meters' statistics; delete those statistics from the Repairs panel (or the statistics tool). The week and month totals start from zero and build up day by day; if you want the current month to start from your real figures, type them into the `input_number` helpers once.
- **The EV load change and the EV sensors were verified on a US cube.** If you have an EU cube with a charger and the sensors don't appear, or the load figure looks wrong while it charges, please open an issue with the `homeDeviceInfo raw=` line from the debug log (redact anything personal).
- The two battery-counter fixes from v1.4.1 (the local-midnight reset and the zero-reading guard) are Eric's too.

### 🧪 Tests

463 passing, 1 skipped, including tests for the EV fields (US and EU flags, and a real US payload), the roll-up automation (a day is added once, last week's days are excluded, it waits for the cloud to roll over) and the standing-charge sensors.

### 📦 Upgrading

1. Update via HACS and restart Home Assistant. No configuration changes are needed; the EV sensors appear on their own if your cube reports a charger.
2. Optional: refresh the dashboard and the weekly package from the repository (see above).

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
