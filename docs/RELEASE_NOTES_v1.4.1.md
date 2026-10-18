## 🩹 EP Cube Integration v1.4.1 — restart-safe freeze baseline

> **TL;DR** — a small fix for one edge case in v1.4.0: if Home Assistant restarted in the middle of a Predbat *freeze*, the cube could end up stuck in Time of Use mode instead of returning to Self-Consumption. No new options, nothing to configure.

### 🔧 Fixed

- **A restart mid-freeze no longer changes your operating mode.** A freeze is written as a short Time of Use slot. If Home Assistant restarted while one was active, the integration lost its record of the mode the cube was in before, then took a fresh snapshot, found the cube in Time of Use, stripped its own leftover slot, and adopted Time of Use as your mode. Every later revert then put the cube back in Time of Use. (With no slots, Time of Use behaves much like Self-Consumption, so nothing misbehaved, but it wasn't the mode you had chosen.) The snapshot now recognises this case, logs a warning, and uses Self-Consumption. This is the same approach the integration already used for a Dynamic export left behind by a restart.
- **Your own Time of Use setup is untouched.** If the cube has slots of your own (with or without a leftover freeze slot), or you use the Dynamic Tariff yourself, that stays as the baseline exactly as before.

### 🧪 Tests

402 passing, 1 skipped, including four new tests for the cases above.

### 📦 Upgrading

1. Update via HACS and restart Home Assistant.
2. If your cube is currently in Time of Use only because of a restart like the one described above, set *Operating mode* back to Self-Consumption once. The fix prevents it happening again; it doesn't change a mode that's already been adopted.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
