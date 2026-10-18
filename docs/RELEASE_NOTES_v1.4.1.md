## 🩹 EP Cube Integration v1.4.1 — restart-safe freeze baseline and steadier battery counters

> **TL;DR** — three small fixes, no new options and nothing to configure. A restart in the middle of a Predbat *freeze* could leave the cube in Time of Use instead of Self-Consumption, and the daily battery charged / discharged counters reset at the wrong time and could book a phantom full cycle. The two counter fixes are from [Eric Fisher (@eafisher11)](https://github.com/eafisher11/ha-ep-cube). Thank you, Eric.

### 🔧 Fixed

- **A restart mid-freeze no longer changes your operating mode.** A freeze is written as a short Time of Use slot. If Home Assistant restarted while one was active, the integration lost its record of the mode the cube was in before, then took a fresh snapshot, found the cube in Time of Use, stripped its own leftover slot, and adopted Time of Use as your mode. Every later revert then put the cube back in Time of Use. (With no slots, Time of Use behaves much like Self-Consumption, so nothing misbehaved, but it wasn't the mode you had chosen.) The snapshot now recognises this case, logs a warning, and uses Self-Consumption. This is the same approach the integration already used for a Dynamic export left behind by a restart.
- **Your own Time of Use setup is untouched.** If the cube has slots of your own (with or without a leftover freeze slot), or you use the Dynamic Tariff yourself, that stays as the baseline exactly as before.
- **The daily battery charged / discharged counters reset at Home Assistant's local midnight.** They used the operating system's date, which is UTC in many containers, so for a UK install they rolled at 01:00 in summer rather than at midnight.
- **A 0 kWh battery reading around the cloud's midnight rollover is ignored.** The cloud briefly reports 0 (or drops the field), which made the battery energy plunge to zero and booked the whole pack as discharged and recharged within two polls. A 0 is now treated as "no reading": the sensor goes unavailable for that poll and the counters keep their last good value.

### 🧪 Tests

416 passing, 1 skipped, including four new tests for the freeze baseline, one for the local-midnight reset in a non-UTC time zone, and tests for the zero-reading handling.

### 📦 Upgrading

1. Update via HACS and restart Home Assistant.
2. If your cube is currently in Time of Use only because of a restart like the one described above, set *Operating mode* back to Self-Consumption once. The fix prevents it happening again; it doesn't change a mode that's already been adopted.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
