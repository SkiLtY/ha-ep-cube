## ✨ EP Cube Integration v1.6.0 — counters that survive a restart, plus eco figures and Dynamic thresholds

> **TL;DR** — the *Battery charged today* and *Battery discharged today* counters no longer start again from zero when Home Assistant restarts ([#3](https://github.com/SkiLtY/ha-ep-cube/issues/3)). Also new: the app's coal and tree figures as sensors ([#10](https://github.com/SkiLtY/ha-ep-cube/issues/10)), and the Dynamic tariff's buy-below and sell-above prices ([#11](https://github.com/SkiLtY/ha-ep-cube/issues/11)). Nothing to configure.

### 🔧 Fixed

- **The daily battery counters keep their value across a restart.** The cube doesn't report charge and discharge separately, so the integration builds those totals by watching the battery's stored energy change between polls. That running total lived only in memory, so any restart (an update, a reboot, a config reload) set it back to zero for the rest of the day. It is now saved and put back.
- **Changes while Home Assistant was off are counted.** The last reading is saved too, so the first poll after a restart counts the net change since then, rather than skipping it.
- **Yesterday's numbers are never carried over.** A saved total is only used if it was saved today (Home Assistant's local day); anything older, or anything unreadable, is ignored and the counters start fresh as before.

### ✨ New

- **Eco figures:** `coal_today`, `coal_year`, `trees_today`, `trees_year`. These are the EP Cube app's own estimates, not measurements: against a live payload, coal is exactly 0.4 and trees 0.055 per kWh of home consumption. The cloud doesn't state coal's unit; the sensors say kg (the usual coal-equivalent figure). They have no device class, so Home Assistant never converts them.
- **Dynamic tariff thresholds:** `dynamic_buy_below` and `dynamic_sell_above` (pence per kWh, under diagnostics). They show what the cube is set to, whether set in the app or by the integration during a Dynamic export or charge. Outside Dynamic mode the cloud holds meaningless placeholders (0 and 999.99), so the sensors read unavailable rather than showing them. Writing these prices is a separate, later change.

### 🧪 Tests

497 passing, 1 skipped, including new tests for the counter save and restore, the eco values, and the threshold sensors in and out of Dynamic mode.

### 📦 Upgrading

1. Update via HACS and restart Home Assistant.
2. The first restart starts the battery counters from zero as before (there is nothing saved yet); from then on restarts keep the day's totals.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
