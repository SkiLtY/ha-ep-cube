## 🛠 EP Cube Integration v0.6.4 — TOU Schedule Edits Always Land

Fixes a real silent-drop bug surfaced during 2026-05-29 live-cube testing: editing the TOU schedule while the cube was in self-consumption or backup mode would claim "Schedule saved" but actually do nothing. The cube's documented [TOU → non-TOU transition quirk](https://github.com/SkiLtY/ha-ep-cube/blob/main/custom_components/ep_cube/services.py) extends to **any** schedule write made while the cube isn't already in TOU mode — the HTTP call returns 200 but the cube silently ignores the tier-list portion of the payload.

### ✨ What's new

- **2-write dance in `ep_cube.set_tou_schedule`** — when the cube is in non-TOU mode and the user doesn't tick *"Switch to TOU when saving"*, the service now performs two writes:
  1. **Write A**: flip to TOU mode + apply new tier lists (cube honours both because TOU → TOU never drops the write)
  2. **Write B**: flip back to the user's original mode (cube switches mode, drops the tier-list write — but the tier lists from A are already on the cube)

  Net result: schedule edits always land on the cube regardless of current mode, with no user-visible mode change for the operator. Single-write behaviour preserved when the cube is already in TOU mode or when the user explicitly wants to end up in TOU.

- **"Clear all" button on the editor card** — one-click wipe of every slot on both workday + weekend profiles. Confirmation modal before save. Useful for starting fresh, removing leftover shim slots from prior Predbat overrides, or preparing for an auto-paint pass (Phase 4.3 — coming v0.8). Calls the same `set_tou_schedule` service with empty tier lists, just bypasses the manual remove-one-at-a-time loop.

### 🐛 Fixed

- **Silent-drop on save while in non-TOU mode** — edits to the TOU schedule no longer require the cube to be in TOU mode at save time. Previously you had to either (a) tick *"Switch to TOU when saving"* first, or (b) manually switch to TOU via the operating-mode dropdown, edit, then switch back. Now any save just works.
- **Leftover shim slots from prior Predbat / `debug_freeze` overrides auto-clean on next save** — because every `set_tou_schedule` now writes via TOU mode briefly, leftover shim-signature slots in the cube's TOU memory get stripped from `live_clean` before write A goes out. So one save (even an empty one via the new "Clear all" button) clears any accumulated cruft.

### 🧪 Tests

Four new pytest cases in `tests/test_set_tou_schedule.py::TestServiceDispatch` covering the 2-write decision matrix:

- 2-write when cube is non-TOU + no `switch_to_tou` flag
- Single write when `switch_to_tou=True` (user wants TOU end-state)
- Single write when cube is already in TOU mode
- The user's specific scenario: clearing slots from self-consumption mode lands correctly

Existing tests updated where the await-count assertion changed from 1 to 2. Suite now 183 tests, all green.

### 📦 Upgrading

- **HACS users**: bump from v0.6.3 → v0.6.4 in the HACS UI.
- **Manual users**: re-copy `custom_components/ep_cube/` + `www/ep-cube-tou-editor.js` from the release zip; browser hard-refresh for the card.
- **HA restart required** — Python-side service changes.

### 📋 Behaviour change to note

Saves now cost 2 cloud writes (was 1) when the cube isn't already in TOU mode. Writes are cheap and infrequent (users edit schedules occasionally, not minutely), so this is well within the cube's tolerance — but worth flagging for anyone monitoring write rates.

If the cube rejects write B (rare), the cube is left in TOU mode rather than the user's original mode. The service raises `HomeAssistantError` with a message explaining this, so it's visible in the UI rather than silent.

### 🛣 What's next

- **v0.7** — per-tier rate entry on the editor card (chipped); drop legacy shim-price tokens from migration window.
- **v0.7** — `queryDataElectricityV2` cloud-stats expansion.
- **v0.8** — auto-paint TOU schedule from Predbat's low-rate threshold + Agile forecast.

### ☕ Support

If this saves you the mitmproxy sessions and Python hours it took to build, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
