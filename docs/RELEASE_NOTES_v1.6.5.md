## 🏷️ EP Cube Integration v1.6.5 — connection type, and a quieter log

> **TL;DR** — a new diagnostic sensor shows whether your cube is on Wi-Fi or Ethernet, and cubes without a Dynamic tariff no longer log an error every few minutes ([#23](https://github.com/SkiLtY/ha-ep-cube/issues/23)).

### ✨ New

- **Connection type sensor.** Shows Wi-Fi or Ethernet, taken from the cloud's `networking` value (1 is Wi-Fi, 3 is Ethernet). Any other value reads "Unknown", with the raw code in the `networking_code` attribute so it can be worked out. A cube on Ethernet reports a signal level of 0, which is expected.
- Translated into German, Italian and Dutch.

### 🐛 Fixed

- **No more error in the log without a Dynamic tariff.** The cloud only has prices for the Dynamic tariff, so without one it answers the price request with an error, and the integration logged that as a failure. It is now treated as "no Dynamic prices": the price sensors stay empty and the message is debug-level only.

### 📦 Upgrading

Update via HACS and restart Home Assistant. Nothing to configure.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
