# Phase 3.2 — Mobile-app API spike (Bearer-token replacement for JSESSIONID)

> Research spike, 2026-05-21. Goal: assess whether replacing our current `JSESSIONID`-cookie auth with the mobile-app Bearer-token surface (as used by [Bobsilvio's epcube integration](https://github.com/Bobsilvio/epcube)) would kill the "paste a fresh cookie every hour" UX without trading it for something equally bad.
>
> **Verdict: conditional GO.** The token surface eliminates the cookie-paste, the captcha is solvable programmatically, and the write API is actually *simpler* than what we use today. One material risk (write-revocation on mobile-app open) needs an explicit mitigation. Worth doing before Phase 4 (HACS).

## TL;DR

| Question | Answer |
|---|---|
| Does the mobile-app API cover our 9-sensor read contract? | **Yes** — `homeDeviceInfo` + `getSwitchMode` + `deviceList` exist verbatim on `/api/device/*`, same response shapes. |
| Does it cover our 7-service write contract? | **Yes, unified but not strictly simpler** — one `POST /api/device/switchMode` replaces three split endpoints, *but* it requires the full payload on every call (vs `setSelfConsumption` which only needed 3 fields). Confirmed 2026-05-21: 3-field minimal call returns `500 "The parameter cannot be null ：weatherWatch"`. Trade-off: simpler *code* structure (one method to maintain), more payload-building per call. |
| Does it eliminate the JSESSIONID-paste-every-hour UX? | **Yes**, one-time email+password in config-flow replaces hourly cookie paste. Captcha is solved programmatically (template matching + AES-ECB encrypt). |
| Token lifetime? | **Undocumented** but issue tracker shows no chronic re-auth complaints — empirically days-to-weeks, not hours. |
| Show-stopper risks? | **One**: opening the official mobile app silently revokes write privileges on the HA token. Reads keep working; writes 403. Mitigation required before this is production-ready for Predbat. |
| Effort estimate | ~1–2 sessions to refactor [api.py](../custom_components/ep_cube/api.py), add a captcha-solving auth flow to [config_flow.py](../custom_components/ep_cube/config_flow.py), and update the mock to mirror the new surface. |

## Auth flow (the part that replaces JSESSIONID paste)

Bobsilvio's token tool ([source](https://github.com/Bobsilvio/epcube-token), [live app](https://epcube-token.streamlit.app/)) implements a 3-POST flow reverse-engineered from the iOS app:

```
1. POST /api/open/common/captcha/get        { clientUid: <uuid> }
   → returns { originalImageBase64, jigsawImageBase64, secretKey, token }

2. (client-side) locate the puzzle-piece position in the background image
   using OpenCV-style normalized cross-correlation template matching,
   then AES-ECB-encrypt { x, y } with secretKey, base64-encode the result.

3. POST /api/open/common/captcha/check      { clientUid, token, pointJson: <encrypted> }
   → returns verification token

4. POST /api/open/common/login              { userName, password, captchaVerification }
   → returns { data: { token: <Bearer> } }
```

The headers used throughout (verbatim from the mobile-app traffic):

```
User-Agent: ReservoirMonitoring/2.1.0 (iPhone; iOS 18.3.2; Scale/3.00)
Accept: */*
Content-Type: application/json
Accept-Encoding: gzip, deflate, br
Accept-Language: it-IT
```

After login, every subsequent request to `/api/device/*` carries `Authorization: Bearer <token>` (**with** the `Bearer ` prefix — confirmed empirically 2026-05-21 against `/api/device/deviceList`; raw-token form returns the canonical misleading 403 `"User token expired"`). Bobsilvio's tool uses raw-token form, but that targets `/api/open/*` endpoints which may have different auth shape — needs separate verification if we touch those paths.

`Accept-Language: en-GB` should be used in our integration (the server localizes error bodies by request locale — `it-IT` returns Italian; we want English).

**Implication for our config-flow**: user pastes email + password once, integration runs the 3-step flow on submit, stores the resulting token. On 401/403 (with the right marker), re-run the flow silently using stored credentials. No more hourly portal trip.

**Dependency cost**: the captcha-solving needs an image-processing primitive. Bobsilvio bundles a numpy/cv2 dependency. We can either:
- Vendor a tiny single-file template-matcher (numpy-only — feasible because it's just normalized cross-correlation on two small base64-decoded PNGs)
- Add `numpy` + `opencv-python-headless` to manifest (HA already ships numpy; opencv adds ~30 MB)

**Resolved 2026-05-21 (numpy-only feasibility spike):** Pure-numpy port is bit-exact with `cv2.matchTemplate(..., TM_CCOEFF_NORMED)` and hits the same 80% cube-acceptance rate as Bobsilvio's cv2 implementation. ~85 LOC including helpers. See [spikes/captcha_spike.py](../spikes/captcha_spike.py) and the spike-results section below.

Dependencies for the final integration:
- `numpy` — already an HA runtime dep, no manifest change
- `Pillow` — already an HA runtime dep, no manifest change
- `pycryptodome` — new dep, ~1.8 MB, widely used by other HA integrations (`hue`, `roborock`, etc.)

Total new footprint: pycryptodome only. Opencv-headless avoided.

## Endpoint map: what we do now vs what we'd do

Both surfaces hit the **same host** (`monitoring-eu.epcube.com`), just different path prefixes and auth schemes.

| Today (`/v1/api/home/*` + JSESSIONID cookie) | Mobile (`/api/device/*` + Bearer header) | Notes |
|---|---|---|
| `GET /v1/api/home/homeDeviceInfo?sgSn=` | `GET /api/device/homeDeviceInfo?dayMonthYearFormat=YYYY-MM-DD&sgSn=` | Same response shape, mobile path adds a date query param |
| `GET /v1/api/home/getSwitchMode?devId=` | `GET /api/device/getSwitchMode?devId=` | Identical |
| `GET /v1/api/home/deviceList` | `GET /api/device/deviceList` | Identical |
| `GET /v1/api/home/getSellingConfig/{devId}` | **Not available** (404 on both path-param and query-param forms, confirmed 2026-05-21) | **Non-issue**: `get_selling_config` is dead code in our integration — defined in [api.py:347](../custom_components/ep_cube/api.py) but never called. Drop during refactor. |
| `POST /v1/api/home/setSelfConsumption` | `POST /api/device/switchMode` (workStatus=1) | **Three endpoints collapse into one** |
| `POST /v1/api/home/setBackUp` | `POST /api/device/switchMode` (workStatus=3) | |
| `POST /v1/api/home/setTimOfUse` | `POST /api/device/switchMode` (workStatus=2, onlySave=0) | ⚠️ `onlySave` is **persistent state on the cube**, not a per-call flag (confirmed 2026-05-21 by writing `onlySave:"1"` and seeing it stick in the next `getSwitchMode` read). Our integration must explicitly send `onlySave:"0"` on every write to avoid leaving the cube in save-only mode. The spike-doc earlier framing of "handy for shim" was wrong. |
| `GET /v1/api/home/clearTouMode?devId=` | `POST /api/device/switchMode` with empty time lists | No dedicated clear endpoint |
| — | `GET /api/device/userDeviceInfo?devId=` | Bonus — device details not exposed via web portal |
| — | `GET /api/device/queryDataElectricityV2?devId=&queryDateStr=&scopeType=` | Bonus — historical energy queries with daily/monthly/yearly scope |

**Refactor surface in our code**: [api.py](../custom_components/ep_cube/api.py) `EPCubeClient._request` (auth header), `set_self_consumption`/`set_backup`/`set_tou_schedule` (collapse into one method), [config_flow.py](../custom_components/ep_cube/config_flow.py) (new credential capture + token bootstrap), [mock_server/](../mock_server) (mirror the new endpoint shapes + add captcha endpoints).

## Strict typing requirements (don't fall into Bobsilvio's bug-fix saga)

Documented via [Bobsilvio/epcube#24](https://github.com/Bobsilvio/epcube/issues/24) — the EP Cube cloud is inconsistently strict about JSON types and returns *misleading* `403 "token expired"` when payload typing is wrong rather than a real `400`. Required types for `switchMode`:

| Field | Required type | Wrong-type symptom |
|---|---|---|
| `activeWeek`, `activeWeekNonWorkDay`, `dayLightActiveWeek`, `dayLightActiveWeekNonWorkDay` | `list[str]` (e.g. `["1","2","3","4","5"]`) | 403 "token expired" — JSON parse failure misreported |
| `touType` | native `int` (`0`, not `"0"`) | 500 server crash |
| `dayLightSavingTime` | native `bool` (`true`/`false`, not `"0"`/`"False"`) | 500 server crash |
| `allowChargingXiaGrid`, `weatherWatch`, `onlySave`, `selfConsumptioinReserveSoc`, `backupPowerReserveSoc` | `str` (e.g. `"1"`) | varies |
| `workStatus` | `str` (`"1"`/`"2"`/`"3"`) | as above |
| Time-slot strings | `"HH:MM_HH:MM_<price>"` | parse failure |

Our current [build_tou_payload](../custom_components/ep_cube/api.py) is mostly aligned but uses `int` for `activeWeek`/`activeWeekNonWorkDay` — that's a latent bug to fix in any case. (`workStatus` field on `setSelfConsumption`/`setBackUp` is already a string, good.)

## The risk we planned for: write-revocation on mobile-app open — NOT REPRODUCED

From [Bobsilvio/epcube#24 comments](https://github.com/Bobsilvio/epcube/issues/24):

> "If the EP Cube server detects a secondary login (like opening the official mobile app), it instantly revokes 'Write' privileges for the Home Assistant token, but allows 'Read' privileges to continue working. This means sensor data keeps updating, but mode changes fail. Furthermore, simply clicking 'Reload' on the integration in Home Assistant does not clear the cached token. To properly test a fresh token, the integration must be completely deleted and re-added."

**Why this would matter if true**: Predbat issues overrides via the shim throughout the day. If the user opens the mobile app to glance at SoC and the shim's *next* write silently 403s — while reads keep working, masking the failure to the coordinator — Predbat thinks the cloud is in the override state when the cloud actually isn't. Sticky drift between planned and actual.

### Probes (2026-05-21): hypothesis rejected across four scenarios

Two scripts in [spikes/](../spikes/), four scenarios in total. Every read and every no-op write returned `HTTP 200 / inner status 200 / message "Success"`. Full request/response bodies in `<captures-private>/2026-05-21-revocation-probe.log` and `<captures-private>/2026-05-21-revocation-probe-v2.log` (both gitignored).

| Scenario | Operator action | Result |
|---|---|---|
| v1: app open + passive view | open app, log in, wait for live SoC, ~2 s window | read OK, write OK |
| v2-A: **app-initiated write** | app toggled mode → workStatus=3 (backup); we then wrote workStatus=3 back | read OK, write OK |
| v2-B: sustained app session | app kept open + active, 90 s window with navigation | read OK, write OK |
| v2-C: post-close recovery | app swipe-killed, 5 s settle | read OK, write OK |

The killer test is v2-A: the cube saw *both* sessions writing in sequence (app → ours) and accepted both. Whatever revocation logic Bobsilvio's reporter encountered, it does not fire for our account / region (`monitoring-eu`) / firmware (`02160239021920260323`) under any combination of operator actions we could realistically construct.

### Implication for the implementation plan

The four original mitigations drop from "load-bearing production safety" to "good defensive engineering":

1. **Post-write read-back** in [services.py](../custom_components/ep_cube/services.py) — still wise. Catches any write-failure cause (typing bugs, transient cloud, future revocation behaviour). Keep as the core write-correctness mechanism. Cheap.
2. **Auto re-auth on 403/401** at the `_request` layer — still needed for normal token expiry (days-to-weeks per Bobsilvio's empirical claim). Cheap once the captcha-solve path exists.
3. **HA notification** on repeated re-auth failure — keep.
4. **Predbat-side back-off** — **deferred**. Was the most invasive mitigation; not load-bearing now. Revisit only if production writes start 403-ing in correlation with mobile-app activity.

**If we ever see production write 403s**: re-run [revocation_probe_v2.py](../spikes/revocation_probe_v2.py) to characterise the trigger, then decide whether back-off is needed.

## Captured findings (2026-05-21 capture session)

Captures run against `/api/device/*` with Bearer token issued by [epcube-token.streamlit.app](https://epcube-token.streamlit.app/):

- **`deviceList`** ✅ — full schema match, returned `devId=5613`, `sgSn=100100007001257120126`, capacity `20.0kWh` (4 × 5kWh), embedded `workParam` confirms current state.
- **`getSwitchMode?devId=5613`** ✅ — schema identical to web-portal surface. `workStatus="1"`, `activeWeek=[1,2,3,4,5]` (ints on read), `dayLightSavingTime=false` bool.
- **`homeDeviceInfo?sgSn=...&dayMonthYearFormat=...`** ✅ — **schema delta vs web portal**: values are plain numbers (`gridElectricity:5.01`, `solarPower:93.00`), not `"5.01kWh"` strings. Refactor will be *simpler* — no string-stripping in [api.py](../custom_components/ep_cube/api.py).
- **`getSellingConfig`** ❌ — 404 on both `/api/device/getSellingConfig/{devId}` and `/api/device/getSellingConfig?devId={devId}`. Non-issue: dead code in our integration (see endpoint map).
- **`userDeviceInfo?devId=5613`** ✅ — bonus device metadata (model `EP Cube HES-EU2-S7-20G`, activation date, warranty date, address). No `sellingPowerLimit`/grid-code here either.
- **404 envelope is double-wrapped** — outer `status:200/message:Success`, inner `data.status:404`. Our `_request` error handling must check both layers, not just HTTP status.
- **Validation errors are 500 with the offending field named** — e.g. minimal `switchMode` payload missing `weatherWatch` returned `500 "The parameter cannot be null ：weatherWatch"` (note U+FF1A fullwidth colon — cloud is China-hosted). Important: validation errors come as 500, **not** as the misleading-403 form. So if we see `403 "User token expired"` on a write, it's genuinely auth-related (token expired OR mobile-app write-revocation), not a typing bug. Cleaner error-discrimination than we expected.
- **Full no-op write confirmed working** (2026-05-21) — `switchMode` POST mirroring the captured `getSwitchMode` state (with `activeWeek` ints → list[str] conversion applied) returned `200 Success` and a `getSwitchMode` read-back confirmed state unchanged. End-to-end write path validated.
- **`onlySave` is persisted state, not a per-call flag** — see endpoint-map row for setTimOfUse for full detail. Our integration must explicitly send `onlySave:"0"` on every write.
- **Cube normalizes `activeWeek` on read** — writes require list[str] (`["1","2","3","4","5"]`), reads return list[int] (`[1,2,3,4,5]`). Validates the session-6 `str()` coercion fix in `build_tou_payload`.
- **`weatherWatch` is a forecast-driven pre-charge feature** — when `"1"`, cube pulls weather forecast and pre-charges ahead of predicted storms. Conflicts with Predbat's economic optimization; we must always send `weatherWatch:"0"` on writes.

## Captcha-solver feasibility spike (2026-05-21)

Goal: confirm we can implement the puzzle-piece locator in pure numpy (already an HA dep) and avoid the ~30 MB `opencv-python-headless` dependency.

**Verdict: GO, pure-numpy is bit-exact with cv2 and matches the same 80% accept rate.**

Algorithm shape (~50 LOC for the core matcher, ~85 LOC including decoder helpers):
- `cv2.matchTemplate(..., TM_CCOEFF_NORMED)` becomes integral-image-based windowed sum/sum-of-squares + FFT-based cross-correlation. Standard textbook implementation.
- `cv2.minMaxLoc` is `np.unravel_index(arr.argmax(), arr.shape)`.

Validation:
- **Synthetic** (paste a known patch into noise): exact recovery, score 1.000, 5–9 ms.
- **Side-by-side vs cv2 on 10 live captchas**: numpy x === cv2 x in 8/8 trials that ran clean; scores agree to 3 decimal places. (2 trials hit an unrelated dimension-mismatch crash, fixed below.)
- **Live cube acceptance (20 trials)**: 16/20 = 80% success, ~28–31 ms per match. Identical rate to Bobsilvio's cv2 reference.

Two findings that *aren't* obvious from Bobsilvio's source and matter for our implementation:

1. **Don't `convert("L")` the background.** The EP Cube background PNG is in PIL "P" (palette) mode. The slot's contrast pattern lives in **palette-index space**, not in true-luminance space. Doing the textbook-correct `pil.convert("L")` (which looks up RGB through the palette and applies ITU-R 601 luminance) gives mean=142 and scores ~0.2–0.3 → 0% cube acceptance. Doing `np.array(pil)` directly returns raw palette indices (mean=44) and scores ~0.4–0.8 → 80% acceptance. Bobsilvio's code accidentally hits the right path because `cv2.cvtColor(palette_arr, COLOR_GRAY2BGR)` then `BGR2GRAY` round-trips palette indices unchanged. Our numpy port replicates this on purpose (with a comment explaining why) — see `decode_b64_image_grey` in [captcha_spike.py](../spikes/captcha_spike.py).

2. **Background sometimes 2 px shorter than puzzle.** ~40% of captchas come back with `bg=(350, 612)` and `piece=(352, 94)` — the cube renders bg without the slider track included. Bobsilvio's "swap them if piece bigger" workaround crashes cv2 because after the swap the new "piece" is wider than the new "bg" in the other axis. Fix: pad the bg with its mean value (top + bottom) when the height delta is ≤ 4. Match accuracy is preserved because the added rows align with the puzzle's transparent (black-after-decode) regions.

Performance for production (per attempt):
- Captcha fetch: ~200 ms (network)
- Decode + match: ~30 ms (CPU)
- Encrypt + check: ~150 ms (network)
- Total per attempt: ~400 ms
- With 80% per-attempt success and up to 3 retries, effective success ≈ 99.2%, worst-case login latency ≈ 1.2 s.

Final `match_template_ccoeff_normed`:
```python
def match_template_ccoeff_normed(image, template):
    image = image.astype(np.float64); template = template.astype(np.float64)
    H, W = image.shape; h, w = template.shape; n = h * w
    t_zm = template - template.mean()
    t_norm_sq = float((t_zm ** 2).sum())
    integral = np.zeros((H+1, W+1)); integral[1:, 1:] = image.cumsum(0).cumsum(1)
    integral_sq = np.zeros((H+1, W+1)); integral_sq[1:, 1:] = (image**2).cumsum(0).cumsum(1)
    win_sum = integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]
    win_sq  = integral_sq[h:, w:] - integral_sq[:-h, w:] - integral_sq[h:, :-w] + integral_sq[:-h, :-w]
    win_var_n = np.maximum(win_sq - (win_sum**2) / n, 0.0)
    fft_shape = (H + h - 1, W + w - 1)
    F_I = np.fft.rfft2(image, s=fft_shape)
    F_T = np.fft.rfft2(t_zm[::-1, ::-1], s=fft_shape)
    numerator = np.fft.irfft2(F_I * F_T, s=fft_shape)[h-1:H, w-1:W]
    denom = np.sqrt(t_norm_sq * win_var_n)
    return np.where(denom > 1e-10, numerator / denom, 0.0)
```

## Open questions (to resolve before code starts)

1. **What's the actual 401/403/expiry behaviour?** Need to:
   - Note the timestamp on a fresh token, leave it alone for 24h, retry — does it 401?
   - Try to deliberately invalidate by opening the mobile app, then probe a read and a write — confirm reads keep working and writes 403.
   - Document the response body of a *genuine* write-revocation (vs the misleading 403 from typing errors).
2. ~~**Can captcha-solving be done with numpy alone?**~~ ✅ Resolved 2026-05-21 — yes, see spike section above.
3. **Token storage strategy in HA**: store password in `entry.data` (encrypted at rest by HA) and re-auth on demand, or store token + refresh on 403 by re-prompting user? Decision affects UX significantly.

## Implementation status

**Steps 1-5 complete, end-to-end live on the integration.** Mock-server update (step 6) and docs (step 7) outstanding.

1. ✅ **Capture session** (2026-05-21) — see "Captured findings" above.
2. ✅ **Vendor the captcha-solver** ([commit `dc2292d`](https://github.com/SkiLtY/ha-ep-cube/commit/dc2292d), fixes in [`dad3d0d`](https://github.com/SkiLtY/ha-ep-cube/commit/dad3d0d)) — `custom_components/ep_cube/captcha.py` with the pure-numpy matcher and the full 4-POST login flow. Live-validated via `spikes/captcha_login_spike.py`: captcha solved, 246-byte Bearer returned, `/api/device/deviceList` accepts it.
3. ✅ **Refactor `api.py`** ([commit `d12d911`](https://github.com/SkiLtY/ha-ep-cube/commit/d12d911), three follow-up fixes [`22a8589`](https://github.com/SkiLtY/ha-ep-cube/commit/22a8589), [`b2edab4`](https://github.com/SkiLtY/ha-ep-cube/commit/b2edab4), [`e4c5c36`](https://github.com/SkiLtY/ha-ep-cube/commit/e4c5c36)) — Bearer auth, `/api/device/*` endpoints, unified `switch_mode()`, `weatherWatch:"0"` + `onlySave:"0"` forced, post-write read-back. Live-validated against the user's cube.
4. ✅ **Refactor `config_flow.py`** ([commit `5f2c326`](https://github.com/SkiLtY/ha-ep-cube/commit/5f2c326)) — VERSION 4 schema is `username + password + base_url + optional dev_id/sg_sn`. Runs `captcha.login()` on submit. User reconfigured the integration live and was logged back in immediately.
5. ✅ **Re-auth on 403** ([commit `5f2c326`](https://github.com/SkiLtY/ha-ep-cube/commit/5f2c326)) — `__init__.py` wraps `captcha.make_reauth_callback(...)` in a closure that persists the refreshed token back to `entry.data` via `async_update_entry`, so HA restarts after a refresh don't burn another captcha solve.
6. ⏸️ **Update mock server** — deferred to next session. Mock currently still speaks `/v1/api/home/*`; regression coverage broken until this lands.
7. ⏸️ **Document write-revocation in TROUBLESHOOTING.md** — deferred. Findings already in this file under "The risk we planned for".

**Defer until after**: Phase 3.2 should *not* block Phase 4 (HACS) — but mock-server + TROUBLESHOOTING should land before HACS publication.

## Wire-level discoveries from step 3-5 bring-up (2026-05-21)

These cost real time during the bring-up and are easy to fall back into; documenting so the next refactor doesn't repeat the loop.

### Power fields are centi-kilowatts on `/api/device/homeDeviceInfo`

Mobile-API power fields (`solarPower`, `gridPower`, `backUpPower`, `nonBackUpPower`, …) are fixed-point integers in 0.01 kW units, NOT plain numbers and NOT raw watts.

| Observation | Cube wire value | App display | Unit |
|---|---|---|---|
| Solar at dusk | `solarPower:74` | 0.63 kW | 1 unit = 0.01 kW = 10 W |
| Load | `loadPower:60` | 0.66 kW | same |

`_power_to_w()` in `api.py` multiplies by **10** to convert. The web-portal surface previously returned kW-as-string (`"5.01kW"`) so the old `_kw_str_to_w` multiplied by 1000 — that converter was correct then and would have been right if the spike doc's `solarPower:93.00` value had been interpreted as 0.93 kW = 930 W. Three commits chased this (×1000 → ×1 → ×10).

### `data.status` is a domain field, not an envelope error

`homeDeviceInfo` returns `data.status: "1"` (= device online). An over-eager "double-wrapped 404" inner-envelope check at the `_request` layer treated any non-200 inner `status` as an error and rejected every healthy read. The real 404-double-wrap case (`getSellingConfig` returning `data.status: 404`) is still flagged — the fix is to only treat HTTP-error-shaped inner codes (≥400) as errors.

### aj-captcha `captchaVerification` format

The string passed to `/api/open/common/login` is NOT `token + "---" + pointJson` (where pointJson is the already-encrypted point sent to `/check`). The real format is:

```
captchaVerification = base64(AES_ECB(secret_key, f"{token}---{json({x,y})}"))
```

i.e. AES-encrypt the **literal `token---plaintext_json` string** with the same per-session `secret_key`. The first wrong attempt produced `500 "captcha is overdue, please try again later."` which sounds like a TTL issue but is actually "server can't find a cached verification with this key". Reference implementation: [Bobsilvio/epcube-token app.py](https://github.com/Bobsilvio/epcube-token/blob/main/app.py) `generate_captcha_verification`.

### `/captcha/check` outer "Success" is envelope-level, not captcha-level

The outer `message: "Success"` only means the HTTP envelope reached the captcha service. The real verdict lives inside `data`:

| Field | Success | Failure |
|---|---|---|
| `data.repCode` | `"0000"` | `"6111"` |
| `data.repData.result` | `true` | (field absent) |
| `data.repMsg` | (field absent) | `"验证失败"` (mojibake'd as garbled UTF-8) |
| `data.success` | `true` | (field absent) |

A `message == "Success"` fallback at this layer is a false-positive footgun — it accepts failed captchas and proceeds to `/login` with a bogus verification.

### Validation errors are 500, not 4xx

The cube returns `500 "The parameter cannot be null ：weatherWatch"` (note U+FF1A fullwidth colon — cloud is China-hosted) for missing required fields on `switchMode`. A genuine 403 on a write therefore really means "token rejected" rather than "payload typing wrong" — cleaner error-discrimination than the web-portal surface, which used to return misleading 403 "token expired" for typing errors.

## References

- [Bobsilvio/epcube](https://github.com/Bobsilvio/epcube) — the integration we're learning from
- [Bobsilvio/epcube-token](https://github.com/Bobsilvio/epcube-token) — the captcha-solving Streamlit tool ([live](https://epcube-token.streamlit.app/))
- [Issue #24 in epcube](https://github.com/Bobsilvio/epcube/issues/24) — the strict-typing bug-fix saga + write-revocation note
- Our current contract reference: `<captures-private>/2026-05-20-contract-extract.md` (web-portal surface only — does **not** cover `/api/device/*`)
