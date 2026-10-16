## ⚡ EP Cube Integration v1.4.0 — Forced export and charge via Dynamic Tariff

> **TL;DR** — the cloud has no "export now" command, but the cube's **Dynamic Tariff** mode really does push battery to grid when the sell-above price is low. v1.4 uses it, opt-in, for Predbat's export windows (and optionally its charge windows), with a revert and a watchdog so the cube can't get stuck. It also fixes the HTTP 500 on `switchMode` that some Dynamic-mode users hit, adds per-string PV sensors, lets you pick the Dynamic tariff from Home Assistant, rides out brief cloud errors, and ships a redesigned dashboard (Now / Today / Yesterday / Week). Default behaviour is unchanged until you switch the option on.

Closes the questions in [#1](https://github.com/SkiLtY/ha-ep-cube/issues/1) except where noted under **What's not here yet**.

### ✨ What's new

#### Forced export via Dynamic Tariff (opt-in)

*Settings → Devices & Services → EP Cube → Configure → Force export mode → "Dynamic tariff".* The default stays "TOU peak" (the v1.3 behaviour).

- An export window writes Dynamic mode with `sell above = 0.00p`, so the cube exports until it reaches the **Reserve SoC**, which is set to the SoC at the end of Predbat's window (written exactly, not rounded up).
- At the end of the window the shim reverts in two steps (back to your TOU/Self-Consumption baseline, then parks the Dynamic thresholds at *buy 0 / sell 999.99* so a leftover Dynamic setting can't import or export).
- A **watchdog** checks five minutes after a revert; if the cube is still in Dynamic mode it says so and corrects it.
- If the cube ends up in Dynamic with nothing in charge of it (a restart mid-export, a failed revert), the next snapshot adopts **Self-Consumption**, not the TOU Dynamic left behind.
- A user's *own* Dynamic mode (set in the EP Cube app) is restored safely as the baseline.

> **How it works on the cube.** Dynamic mode compares the thresholds with the sell/buy tariff chosen in the app. Pick a flat export tariff (e.g. "Outgoing Octopus") and "sell above 0.00" simply switches exporting on, whatever the half-hourly prices do; you are still paid your real export rate. See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md#known-limitation-force-export).

#### Forced charge via Dynamic Tariff (opt-in)

*Configure → Force charge mode → "Dynamic tariff".* Predbat's charge windows use *buy below = 999.99* with a Charging Limit. Checks and notifications:

- **`EP Cube: charge did not start`** — raised if the cube hasn't started importing five minutes after the window opens.
- **`EP Cube: no price data`** — raised when the cube reports no valid tariff data (it falls back to Self-Consumption).
- Charge **freeze** is now a *reserve-at-SoC hold* instead of a ToU slot, so a freeze no longer writes a slot to the cube.

#### Choose the Dynamic tariff from Home Assistant

*Configure → "Change tariff".* Pick country, region, supplier and import/export products from the cube's own lists, no app needed. The change is verified by polling the cloud afterwards. Read-only **Import price (cube tariff)** and **Export price (cube tariff)** sensors show the half-hourly prices the cube sees (diagnostic, disabled by default).

#### Per-string PV sensors

Power, voltage and current for each PV string (`PV1`..`PV4`; PV3/PV4 disabled by default) from the portal's `getSolarPvPower`.

#### Reserve (your setting)

`sensor.ep_cube_reserve_your_setting` carries the reserve *you* set, which is the floor Predbat should plan around. Point Predbat's `reserve:` at it (see [`docs/PREDBAT.md`](PREDBAT.md)). Pointing it at the live reserve sensor creates an export stop/restart loop, because the shim itself changes the live reserve during an export.

#### Rides out brief cloud errors

A single `502` from the cloud used to make every entity unavailable for about a minute. The main coordinator now keeps the last good data through up to **3 consecutive** transient failures (5xx, 429, network, timeout) and logs a warning; auth and other errors still fail at once.

#### Dashboard — Now / Today / Yesterday / Week

A full redesign. **Needs more HACS cards**: Power Flow Card Plus, ApexCharts Card, Tabbed Card, Lovelace Card-mod and HTML Jinja2 Template card (see [README → Dashboard](../README.md#-dashboard)).

- **Now** — a "right now" headline (what the cube is doing, battery full by, next charge / hold / export, live price) with a plan strip for the day, the animated power flow and a ±2 h power chart with Predbat's forecast dotted. Holds (Predbat freezes) are drawn apart from real exports.
- **Today / Yesterday** — Power / Battery / Prices charts plus energy-cost and energy cards.
- **Week** — the last seven days, with week-to-date energy and cost cards.
- Optional `examples/ha_config/packages/ep_cube_weekly.yaml` adds the weekly meters.

#### Diagnostics (advanced)

New services for testing: `debug_probe` (read-only cloud routes, with name/Wi-Fi/user fields masked), `debug_probe_write`, `debug_freeze`, `debug_dynamic_export` and `debug_dynamic_charge` (bounded forced export / charge in Dynamic mode). They are labelled *Debug* and may change.

### 🔧 Behaviour changes

- **The HTTP 500 on `switchMode` is fixed.** The slot-based TOU override copied `touType: 1` from the live read-back when the cube was in Dynamic mode, which the cloud rejects. TOU writes now force `touType: 0`.
- A manual mode change from `select.ep_cube_operating_mode` now also updates the shim's held baseline, so a later revert doesn't undo it.
- The weekly helper package integrates power with `method: left` (the default *trapezoidal* method invents energy across long flat stretches on a sensor that only updates on change; it produced a phantom ~20 kWh on the first 5-minute step of a grid charge). **If you copied the helper package earlier, switch your Riemann helpers to *Left*.**

### 🧪 Tests

392 passing, 1 skipped, including new end-to-end tests that drive the Dynamic shim through the real client against the mock server, options-flow tests, and tests for the coordinator's transient-error handling. The mock server now models Dynamic mode and its TOU-write quirk, `getSolarPvPower` and the `dynamicPrice` routes.

### 📦 Upgrading

1. Update via HACS and restart Home Assistant.
2. Nothing changes until you choose **Force export mode / Force charge mode → Dynamic tariff** in Configure. If you do, check the cube's sell tariff is a flat export tariff, and read [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) first.
3. If you use the dashboard, install the extra HACS cards, then re-paste `dashboards/ep_cube.yaml`.
4. If you run Predbat: use `battery_rate_max` in watts, and set `reserve:` to `sensor.ep_cube_reserve_your_setting`. See [`docs/PREDBAT.md`](PREDBAT.md).

### 🚫 What's not here yet

- **Price thresholds as entities.** The shim sets *buy below / sell above* itself and they are not exposed as Home Assistant entities.
- **A supported way to schedule exports without Predbat.** `charge_start` / `discharge_start` read Predbat's plan and take no parameters. The `debug_dynamic_*` services take a duration, reserve and sell-above and work for automations today, but are not yet stable.
- **Forced battery-to-grid in TOU mode.** Not possible: the cloud has no "export now" command. Dynamic mode is the route.

### 🛣 What's next

- Stable, documented services for forced export and charge outside Predbat, and the Dynamic thresholds as entities.
- Lifetime totals and eco metrics (previously pencilled for v1.4).
- Octopus consumption swap for Predbat.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
