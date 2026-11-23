## 🏷️ EP Cube Integration v1.8.1 — Time of Use schedule sensor

> **TL;DR** — a new read-only sensor shows the cube's Time of Use schedule, so a dashboard or automation can see which band is running and when it next changes ([#26](https://github.com/SkiLtY/ha-ep-cube/issues/26)). Nothing else changes.

### ✨ New

- **`sensor.ep_cube_tou_schedule`.** Its state is the band in force right now: **Off-peak**, **Mid-peak**, **Peak**, or **No period** when no slot covers this time. Attributes:
  - `periods` — every slot the cube holds, each with `days` (`work_days` or `non_work_days`), `band`, `start`, `end` and `price`;
  - `next_change` and `next_band` — when the band next changes and what it changes to (looking up to a week ahead);
  - `current_price` — the price of the slot now, as the cube stores it;
  - `schedule_in_use` — `true` while the cube is in Time of Use mode, which is when the bands apply;
  - `clock_change_set` — `true` while the cube is using its clock-change set of slots.
- It reads the same `getSwitchMode` reply the integration already fetches every minute, so there are no extra cloud calls. It is a diagnostic sensor and read-only: nothing is written to the cube.

### ⚠️ Worth knowing

- **Bands and prices, not actions.** The cloud sends time bands with prices, not "charge" or "discharge" or a target SoC per period, so the sensor doesn't invent them. What a band does is for your own automations.
- **Dynamic tariff.** With the Dynamic tariff the schedule lists are placeholders, so the sensor will show a band that means nothing. `schedule_in_use` will be `false` then.
- **Tested on fixtures and the mock cloud**, including slots that cross midnight, non-work days and the clock-change set. Not yet seen on a cube that is actually running a Time of Use schedule, so if the bands look wrong against your EP Cube app, please say so on the issue.
- Editing the schedule from Home Assistant is not part of this release.

### 📦 Upgrading

Update via HACS and restart Home Assistant. The new sensor appears on its own, under the EP Cube device's diagnostic entities.

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
