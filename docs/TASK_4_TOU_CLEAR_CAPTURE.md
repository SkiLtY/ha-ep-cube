# Task #4 — capture the EP Cube mobile app's TOU "Clear" wire-call

> Goal: find what the mobile app emits when the user clears/cancels a TOU
> schedule, so the shim's `_revert_to_baseline` can become a **single** write
> instead of the current strip-slots-then-mode-switch two-write workaround.

## Why this matters

[`services.py::_revert_to_baseline`](../custom_components/ep_cube/services.py)
currently posts **twice** because the cube's cloud silently ignores
tier-list diffs on TOU → non-TOU mode transitions (discovered 2026-05-21
during live-cube verification). The workaround:

1. POST `switchMode` with cleaned schedule + `workStatus=2` (stay in TOU) →
   cube applies the schedule diff because there's no mode transition.
2. POST `switchMode` with `workStatus=baseline` → mode-only transition,
   schedule write ignored but already cleaned in step 1.

Two writes per revert means the shim's cloud-write budget of ≤12/day is
chewed through twice as fast on busy Predbat days, and the window between
the two POSTs is a small but real state-mismatch hazard.

**Hypothesis:** the mobile app has a "Clear" / "Cancel" / "Reset"
button on the TOU screen that either (a) hits a dedicated endpoint
(`clearTou` / `delTimOfUse` / `resetTou` are plausible names — none of
these are in our current capture corpus), or (b) sends a single
`switchMode` POST with empty slot lists + `workStatus=1` that the cube
honours differently from how it treats our equivalent payload.

If (a): swap the two-write revert for a single endpoint call. **Big win.**

If (b): there's some flag we're missing — maybe `onlySave`, maybe a TOU
sub-field. Find it, use it.

If the app also does two writes: task #4 becomes a documented dead-end
("the two-write pattern is the only way") and we close it.

## Pre-flight

mitmproxy + iPad setup: full walkthrough in
[MITMPROXY_SETUP.md](MITMPROXY_SETUP.md). Phase 3 captures used
HAR-from-web-portal and didn't need mitmproxy, so this is the first
session that exercises the iPad route. Allow ~15 min for the one-time
install + cert trust before starting the capture proper.

Use the **iPad**, not the Pixel — stock Android 15 makes user-CA trust
genuinely painful (rationale in MITMPROXY_SETUP.md).

If the EP Cube iOS app turns out to be cert-pinning (smoke test in
MITMPROXY_SETUP.md step 8 will reveal this), fall back to a HAR
capture from the web portal at `monitoring-eu.epcube.com` instead —
but the Clear button may not be exposed there.

## Capture procedure

1. **Start the proxy**, writing to a fresh file:
   ```powershell
   mitmdump -w <captures-private>/2026-05-22-tou-clear.mitm
   ```

2. **Establish a known TOU state.** Open the EP Cube app, sign in, go to
   the TOU schedule screen. If the cube is not already in TOU with at
   least one slot defined, set one slot (e.g. peak 16:00–19:00 £0.40)
   and save — this gives us a "before" state to compare against and a
   reference `setTimOfUse` POST for the control. The mode should show
   as TOU / Time of Use after this.

3. **Note in HA** the value of `sensor.ep_cube_operating_mode` before
   pressing Clear. (Should be `time_of_use`.) Also worth glancing at
   the entity's `selfConsumptioinReserveSoc` attribute so we know the
   baseline reserve value.

4. **Press Clear** (or "Cancel" / "Reset" / "Cancella" — whatever the
   button is called in the app's TOU screen). Confirm any dialog.

5. **Watch the cube settle.** Wait ~10–30 seconds. The app will refresh.
   Note what mode the cube ended up in (probably self-consumption, but
   that's an assumption — record what actually happens). Glance at
   `sensor.ep_cube_operating_mode` in HA — does it match?

6. **Stop the proxy** (Ctrl-C).

## What to capture in addition

If the app exposes other ways to "exit TOU", capture each one as its
own labelled session. Likely candidates:

- The mode-switcher elsewhere in the app (Self-consumption / TOU /
  Backup toggle) — does flipping away from TOU here clear the slots,
  or does it leave them in place (matching our experience)?
- Long-press / swipe-to-delete on an individual slot row — single-slot
  delete might be a different endpoint than clear-all.

Write each session to its own `.mitm` file so we can analyse them
independently.

## What I'll look for in the flow file

When you let me know the file is dropped in `<captures-private>/`, I'll:

1. List all unique `POST`/`PUT`/`DELETE` paths in the flow — surfacing
   any new endpoint we haven't seen.
2. For the request that immediately preceded the cube's mode change,
   diff its payload against our last known `setTimOfUse`/`switchMode`
   bodies to see what changed structurally.
3. Confirm the response shape (presumably the standard `{status:200,
   message:"Success"}` envelope, but might not be).
4. Update [`<captures-private>/2026-05-20-tou-extract.md`](../<captures-private>/2026-05-20-tou-extract.md)
   open-question #13 to "answered" with the finding.

## Code change (post-capture)

Assuming we find a one-write path, the change is small:

- Add a new method to [`api.py::EPCubeClient`](../custom_components/ep_cube/api.py)
  — e.g. `clear_tou_schedule()` or `revert_to_self_consumption()`
  depending on what we learn — that wraps the discovered endpoint.
- Rewrite [`services.py::_revert_to_baseline`](../custom_components/ep_cube/services.py)
  to call it directly, dropping the two-step dance.
- Update the mock server (`mock_server/main.py`) to honour the same
  endpoint so regression coverage survives.
- Update [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)'s "Cube ignores
  tier-list diffs on TOU→non-TOU" row to note the new clean path.

---

## Resolution (2026-05-22, session 14)

### What we found

**iPad mitmproxy route is dead** — the EP Cube iOS app cert-pins, so
TLS handshakes fail under mitmproxy regardless of OS trust store
config. Pixel 10 Pro would compound this with Android's user-CA
hostility, so we fell back to a HAR capture from the web portal at
`monitoring-eu.epcube.com`. Captured as
`<captures-private>/2026-05-22-tou-clear.har` (local-only — `.har` is gitignored
because the file contains auth cookies).

**The "Clear" button is purely client-side.** The HAR showed no
endpoint hits for the Clear action — it just resets the browser
form fields. Canadian Solar's data model doesn't have a server-side
clear concept.

**The web portal uses a per-mode endpoint family** with vastly smaller
payloads than the unified `/api/device/switchMode`:

```
POST /v1/api/home/setSelfConsumption
body: {"devId":"5613","workStatus":"1","selfConsumptioinReserveSoc":20}
→ 200 {"status":200, "message":"Success"}
```

**But the mobile-app surface doesn't mirror it.** Blind probe via
[`spikes/per_mode_endpoint_probe.py`](../spikes/per_mode_endpoint_probe.py):

```
POST /api/device/setSelfConsumption (Bearer auth)
→ 404 {"message":"[/device/setSelfConsumption] Not Found"}
```

So the per-mode family is web-portal-only. We can't reach it from the
mobile-app surface our integration uses, and we don't want to re-add
the JSESSIONID web-portal auth path that Phase 3.2 just removed.

### What we shipped

**Drop step 1 of the revert.** The cleanup write that scrubbed
shim-signature slots from the cube's TOU memory was always cosmetic —
[`services.py::_snapshot_baseline()`](../custom_components/ep_cube/services.py)
already strips stale shim slots on the next override's baseline
capture (session 13's `_strip_shim_slots`). Functionally, dropping
step 1 changes nothing: the cube ends up in the right mode, and the
leftover slots get cleaned on the next Predbat activation.

Cosmetic cost: a user who opens the EP Cube app between override
sessions will see TOU memory slots at synthetic prices (£0.01, £0.20,
or £1.00). User-facing constraint already documented in
TROUBLESHOOTING.md — don't manually configure those prices.

Cloud-write budget impact: revert is now 1 write instead of 2.
≤12/day target effectively doubles to ≤24/day capacity, or — more
realistically — drops actual usage to half its previous level.

### Open follow-up

`mock_server/main.py` does not currently simulate the
TOU→non-TOU tier-list quirk (mock applies all writes faithfully).
Live verification via `ep_cube.debug_freeze` is the only true test.
Mock regression remains valid for the broader override→revert flow,
just blind to this specific quirk. Documented as known mock gap;
not adding mock coverage because the quirk's only consequence (stale
shim slots in TOU memory) is now intentional rather than a bug.

Total change should be ≤100 LOC across integration + mock.

## Deliverable

Drop the `.mitm` file (or `.har` if web portal) into `<captures-private>/` with
the date prefix, then tell me "capture done, look at
`<captures-private>/2026-05-22-tou-clear.mitm`" — I'll take it from there.
