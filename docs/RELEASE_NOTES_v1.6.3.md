## 🩺 EP Cube Integration v1.6.3 — status flags, firmware, model and serial number

> **TL;DR** — the cube's online, fault and alert flags, its firmware version, model and serial number now show up in Home Assistant ([#19](https://github.com/SkiLtY/ha-ep-cube/issues/19)). They're diagnostics, so nothing changes on your dashboards unless you add them. Nothing to configure.

### ✨ New

- **Binary sensors (diagnostic):** Online, Fault, Alert and Firmware auto-update.
- **Sensors (diagnostic):** firmware version, comms-module version, model, serial number, signal level, system status, charge limit SoC and winter mode. The fault warning code is there too, but hidden by default, because we don't know what its codes mean. Unhide it if you want it. Three more whose meaning we don't know yet (the `lpp` and `lpc` durations and backup loads mode) and the comms-module serial number are switched off until you enable them.
- **The device page** now shows the model, serial number and firmware version.
- The owner's name and address, which the same cloud response carries, are never read.
- **The sample dashboard** gains **Device status** (traffic-light icons for Online, Fault and Alert) and **Device info** (model, serial number, firmware, warranty end date) cards on the Control tab, and a tidier **Battery health** card. The dashboard isn't part of the HACS download, so copy the new `dashboards/ep_cube.yaml` over yours if you use it.

### 🔧 Fixed

- **Cycles and lifetime totals read 0 for a minute after a restart.** The lifetime discharged total is now seeded before the first reading is built, so the lifetime and cycles sensors are right straight away.
- **A Home Assistant deprecation warning** about how the device page is looked up, which would have stopped working in HA 2027.8.

### 🧪 Tests

590 passing, none skipped. The end-to-end tests now fail, rather than skip, if their dependencies are missing.

### 📦 Upgrading

Update via HACS and restart Home Assistant. The new entities appear by themselves.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
