## 🏷️ EP Cube Integration v1.6.7 — battery capacity for first-generation systems

> **TL;DR** — first-generation (EP Cube 1.0) systems now report their real battery capacity instead of 0 kWh. Second-generation systems are unchanged.

### 🐛 Fixed

- **Battery capacity showed 0 kWh on Gen 1 systems.** First-generation systems don't send a system capacity, so the integration had nothing to show. It now counts the battery modules the cloud lists for the system and multiplies by 3.32 kWh per module, so a 12-module system reports 39.8 kWh, which matches the datasheet. If the cloud does send a system capacity, that is still used as before.
- **Capacity is kept up to date.** It used to be read once when the integration was set up, so an existing entry kept whatever it found then. It is now read before the first status update and again once a day. If the cloud can't be reached, the integration tries again on the next poll. If the system isn't listed, or has no usable capacity, it logs a warning.

Thank you to [@eafisher11](https://github.com/eafisher11) for finding this on a US first-generation system, and for the fix and the Gen 1 module data ([#24](https://github.com/SkiLtY/ha-ep-cube/issues/24), [#25](https://github.com/SkiLtY/ha-ep-cube/pull/25)).

### 📦 Upgrading

Update via HACS and restart Home Assistant. If you have a Gen 1 system, the battery capacity sensor should show the right figure after the restart.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
