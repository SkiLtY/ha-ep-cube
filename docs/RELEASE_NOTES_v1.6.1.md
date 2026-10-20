## 🩹 EP Cube Integration v1.6.1 — a clear warning for freeze export, which the cube can't do

> **TL;DR** — Predbat's "freeze export" (hold the battery and send surplus solar to the grid) isn't something the EP Cube can do: the surplus still charges the battery. The integration now says so in the log, and the docs explain what to switch off ([#5](https://github.com/SkiLtY/ha-ep-cube/issues/5)). Nothing else changes.

### ✨ What's different

- **A one-time warning.** If Predbat asks for a freeze export, the log now says the cube can't do it and names the switch to turn off, `switch.predbat_set_export_freeze`. The integration still applies the same mid-peak slot as before (no grid charging, no discharge to the house), so nothing about how the cube behaves changes.
- **Docs.** A new troubleshooting row, a note in the example `apps.yaml`, and corrected wording on the mid-peak slot. The vendor's description is "not charging from grid, not supporting loads", which says nothing about solar.
- **Recorder tip.** The troubleshooting list of Predbat entities to exclude from the recorder gains `battery_cycle`, `soc_kw`, `soc_kw_base10` and `soc_kw_best10`.

### 🔎 What we found

On a real cube (20 Oct 2026) with solar 840 W and load 580 W, the battery charged at 260 W and the grid stayed at 0 W under a freeze export. Dynamic tariff can't stand in for it, because it exports at full power and drains the battery.

### 📦 Upgrading

Update via HACS and restart Home Assistant. If you use Predbat, turn off **Set Export Freeze** in the Predbat device. Real exports are not affected.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
