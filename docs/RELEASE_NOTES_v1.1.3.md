## 🪜 EP Cube Integration v1.1.3 — Hassfest Compliance

> **TL;DR** — clearing the way for HACS Default submission. `validate.yml` has been red since v1.0 because `predbat_inverter_mode` select shipped translation keys that don't match Home Assistant's `[a-z0-9-_]+` rule. Dropping the offending state translation block — i18n on "Timed Export" → DE/IT/NL locale labels is lost (Eco stays "Eco" in every language regardless). No runtime behaviour change; this is a CI-pipeline fix.

### 🐛 What's fixed

**Hassfest no longer fails on `Invalid translation key 'Eco'`** at `data['entity']['select']['predbat_inverter_mode']['state']`. The select's wire-format values (`"Eco"` / `"Timed Export"`) are dictated by Predbat — `predbat/inverter.py:2212,2214` does case-sensitive string equality against `"Eco"` and `"Timed Export"`, so we can't lowercase the state values without breaking the Predbat bridge. The only fix that satisfies both constraints is dropping the `state` translation block entirely. HA falls back to displaying the raw state values, which is fine since "Eco" is "Eco" in every locale anyway and "Timed Export" is a Predbat-specific technical term.

Lost: localized labels for "Timed Export" (Zeitgesteuerter Export / Esportazione temporizzata / Geplande export). If you'd like them back, the fix path is HA-side — open an issue against home-assistant/core to allow uppercase translation keys for entity state values, or against predbat/predbat to make Predbat's state matcher case-insensitive.

### 📦 Upgrading

- **HACS users**: bump v1.1.2 → v1.1.3. Restart HA. If you're in `de` / `it` / `nl` and your `select.ep_cube_predbat_inverter_mode` dropdown showed translated labels for "Timed Export", they'll revert to the raw English string.
- **Manual users**: re-copy `custom_components/ep_cube/`. Restart HA.

### 🛣 What's next

**HACS Default submission immediately after this release lands** — validate.yml goes green, the smoke test from v1.1.2 cleared the install path, and we have a real-cube-verified production deployment for ~3 weeks. Submission is the only remaining gate.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
