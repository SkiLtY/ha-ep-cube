## ☀️ EP Cube Integration — First Release

A clean-room Home Assistant integration for the **Canadian Solar EP Cube** residential battery, with a Predbat-compatible shim for Octopus Agile tariff optimisation. Built around the EP Cube mobile-app cloud API with full Bearer-token auth and silent re-auth.

### ✨ What you get

- **22 sensors** across battery / power flow / daily energy / mode + reserve / lifetime — every cube state the cloud exposes
- **5 control entities** — `select.ep_cube_operating_mode`, `switch.ep_cube_allow_grid_charge`, `switch.ep_cube_daylight_saving_time`, `number.ep_cube_self_consumption_reserve`, `number.ep_cube_backup_reserve`
- **7 Predbat shim services** — `charge_start` / `charge_stop` / `discharge_start` / `discharge_stop` / `charge_freeze` / `discharge_freeze` / `idle` — with idempotency, lazy baseline snapshotting, and auto-revert at end of every Predbat-planned window
- **Animated Lovelace dashboard** — drop-in YAML that mirrors the EP Cube mobile app via [Power Flow Card Plus](https://github.com/flixlix/power-flow-card-plus); mode-specific control cards swap automatically
- **Multi-region** — 🇪🇺 EU verified live; 🇺🇸 US (`/app-api` prefix), 🇯🇵 JP, and "Other" (escape hatch for AU / CA / custom hosts) supported via config-flow region picker
- **Modern auth** — one-time email + password setup with in-integration captcha solving (pure numpy) and silent re-auth on token expiry; no JSESSIONID-paste UX
- **i18n** — English, German, Italian, Dutch translations
- **Mock cloud server** — full FastAPI mock of the mobile-app surface; develop and test without hardware
- **150-test pytest suite** with Hassfest + HACS validation on every push

### 📦 Installation

HACS auto-install is pending (separate PR to [hacs/default](https://github.com/hacs/default)). For now, manual install:

```bash
cd /path/to/homeassistant/config
mkdir -p custom_components
git clone https://github.com/SkiLtY/ha-ep-cube /tmp/ha-ep-cube
cp -r /tmp/ha-ep-cube/custom_components/ep_cube custom_components/
```

Restart HA, then *Settings → Devices & services → Add integration → search* **Canadian Solar EP Cube** → enter region + EP Cube mobile-app email/password.

Full instructions including Predbat + dashboard setup: [README](https://github.com/SkiLtY/ha-ep-cube#-quick-start).

### ⚠️ Known limitations

- **Force-export not supported** — the cube cloud has no command for active battery → grid export. `discharge_start` uses the closest approximation: a peak-tier TOU slot that refuses grid import and drains the battery to load. Any surplus above load may export if `sellingEnable` permits, but it's not commanded. See [docs/ARCHITECTURE.md](https://github.com/SkiLtY/ha-ep-cube/blob/main/docs/ARCHITECTURE.md).
- **`sensor.ep_cube_grid_today` is direction-ambiguous** — cube-native counter reports total grid throughput as a magnitude (hides import vs export direction on mixed days). For Energy dashboard use the Riemann sensors (`sensor.ep_cube_import_today` / `sensor.ep_cube_export_today`) shipped in the optional helper package (`examples/ha_config/packages/ep_cube.yaml`).
- **US / JP regions untested** — wired and ready, but no live confirmation. Please [open an issue](https://github.com/SkiLtY/ha-ep-cube/issues) with the result if you try them.
- **`set_tou_schedule` service** — planned for v0.6.

### 🛣 What's next

- **v0.6** — TOU schedule editor service + Lovelace editor card
- **v0.7** — `queryDataElectricityV2` cloud-stats expansion (signed grid import/export, `*_yesterday` variants, lifetime totals)
- **v0.8+** — community-reported polish, US/JP region validation
- **v1.0** — after HACS listing + multi-user validation

### ☕ Support

If this saves you the mitmproxy sessions and Python hours it took to build — consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

**Why v0.5.0 not v0.1.0?** This is past the "first throw-over-the-wall" milestone — built on extensive cloud-API capture, mock-first architecture design, and a 13-phase roadmap planned before the first commit (2026-05-03). 11 of 13 phases now complete, the integration has been running against my own cube since commissioning (2026-05-19), and there's a 150-test pytest suite. But it's still **pre-1.0**: API and entity shapes may shift based on real-user feedback before stabilising. Test in a non-production HA setup before relying on it.

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
