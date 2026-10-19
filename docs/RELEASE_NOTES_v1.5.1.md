## 🩹 EP Cube Integration v1.5.1 — battery charged / discharged counters now survive a restart

> **TL;DR** — one fix, nothing to configure. The *Battery charged today* and *Battery discharged today* counters used to start again from zero every time Home Assistant restarted. They are now saved and put back, so a mid-day restart no longer wipes the day's total. ([#3](https://github.com/SkiLtY/ha-ep-cube/issues/3))

### 🔧 Fixed

- **The daily battery counters keep their value across a restart.** The cube doesn't report charge and discharge separately, so the integration builds those totals by watching the battery's stored energy change between polls. That running total lived only in memory, so any restart (an update, a reboot, a config reload) set it back to zero for the rest of the day.
- **Changes while Home Assistant was off are counted.** The last reading is saved too, so the first poll after a restart counts the net change since then, rather than skipping it.
- **Yesterday's numbers are never carried over.** A saved total is only used if it was saved today (Home Assistant's local day); anything older, or anything unreadable, is ignored and the counters start fresh as before.

### 🧪 Tests

485 passing, 1 skipped, including new tests for the save and restore round trip, ignoring yesterday's and malformed data, counting a change during downtime, restoring on setup and writing the store on a poll.

### 📦 Upgrading

1. Update via HACS and restart Home Assistant.
2. The first restart starts from zero as before (there is nothing saved yet); from then on restarts keep the day's totals.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
