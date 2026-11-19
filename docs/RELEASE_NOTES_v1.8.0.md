## 🏷️ EP Cube Integration v1.8.0 — Backup mode as a force charge option

> **TL;DR** — if your cube's Dynamic-tariff grid charge is stuck at a fraction of its normal rate, you can now charge in Backup mode instead. It is opt-in, and nothing changes unless you choose it.

### ✨ New

- **Backup mode as a force charge option.** *Configure → Force charge mode* has a third choice, **Backup mode (charges to Predbat's target, workaround)**. A Predbat charge window, or `ep_cube.force_charge`, then puts the cube in Backup mode with the Backup reserve set to the charge target. The cube grid-charges up to that level, and at the end of the window the integration restores what you had before, as it does for every other forced charge. The same checks apply: a five-minute look that the battery really is charging, and a revert with retries.
- **Why it exists.** On one cube the Dynamic-tariff grid charge was limited to about 1 kW from 23 Oct 2026 (a normal charge is about 6.5 kW). Backup mode, minutes apart on the same cube, charged at 6.3 kW. If your Dynamic charge runs at full rate, you don't need this.

### 🐛 Fixed

- **`charge_start` no longer trusts a stale window.** Predbat's published charge window can be a leftover from an earlier planning pass (10 Nov: 01:00 to 13:30, while the plan charged only to 05:30). A charge now starts only if Predbat's own plan has one running at that moment, and it ends at the plan's end. With no readable plan it does nothing. This is the same guard v1.7.1 gave `charge_freeze`.

### ⚠️ Worth knowing

- **Targets below 50% are raised to 50%.** The EP Cube app doesn't let you choose a Backup reserve under 50%, so the Backup option and the Backup reserve slider stop there. A Predbat target of 30% means the charge goes to 50%, so it can overshoot a little. (The cloud accepted 10% when we tried it on 18 Nov, so this is the app's limit, but 50% is the setting that has run every night, so that is what ships.)
- **Above the reserve, Backup mode behaves like normal running.** Tested on 18 Nov at night: with the battery at 21% and a Backup reserve of 10%, the battery supplied the house and the grid stayed at 0 W. It does not hold the level, so Predbat's own holds do that work. How Backup mode treats surplus solar is not yet checked.
- **Not tested live yet:** a real overnight charge with the new `charge_start` guard. If a planned charge doesn't start, the "planned charge not charging" alert in the watchdog package will say so.

### 📦 Upgrading

Update via HACS and restart Home Assistant. Nothing changes until you pick Backup mode in Configure.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
