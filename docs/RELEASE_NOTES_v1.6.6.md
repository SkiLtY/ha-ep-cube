## 🏷️ EP Cube Integration v1.6.6 — long charges no longer stop early

> **TL;DR** — a Dynamic charge now runs to the end of the whole charge window, not just the first plan slot.

### 🐛 Fixed

- **Charges cut short.** The integration took the end of Predbat's next plan slot as the end of the charge and switched the cube back to Self-Consumption there, for example at 02:00 on a 00:30 to 05:30 charge, while Predbat still reported "Charging" and did not send the start again. It now follows the run of back-to-back charge slots and stops at the end of the run. A slot with a lower limit still ends the run, as before.

### 📦 Upgrading

Update via HACS and restart Home Assistant.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
