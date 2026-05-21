# Phase 3.2 — Mobile-app API spike (Bearer-token replacement for JSESSIONID)

> Research spike, 2026-05-21. Goal: assess whether replacing our current `JSESSIONID`-cookie auth with the mobile-app Bearer-token surface (as used by [Bobsilvio's epcube integration](https://github.com/Bobsilvio/epcube)) would kill the "paste a fresh cookie every hour" UX without trading it for something equally bad.
>
> **Verdict: conditional GO.** The token surface eliminates the cookie-paste, the captcha is solvable programmatically, and the write API is actually *simpler* than what we use today. One material risk (write-revocation on mobile-app open) needs an explicit mitigation. Worth doing before Phase 4 (HACS).

## TL;DR

| Question | Answer |
|---|---|
| Does the mobile-app API cover our 9-sensor read contract? | **Yes** — `homeDeviceInfo` + `getSwitchMode` + `deviceList` exist verbatim on `/api/device/*`, same response shapes. |
| Does it cover our 7-service write contract? | **Yes, and better** — one unified `POST /api/device/switchMode` replaces our three split endpoints (`setSelfConsumption`, `setBackUp`, `setTimOfUse`). |
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

After login, every subsequent request carries `Authorization: <raw_token>` (note: **no `Bearer ` prefix** — verbatim raw token).

**Implication for our config-flow**: user pastes email + password once, integration runs the 3-step flow on submit, stores the resulting token. On 401/403 (with the right marker), re-run the flow silently using stored credentials. No more hourly portal trip.

**Dependency cost**: the captcha-solving needs an image-processing primitive. Bobsilvio bundles a numpy/cv2 dependency. We can either:
- Vendor a tiny single-file template-matcher (numpy-only — feasible because it's just normalized cross-correlation on two small base64-decoded PNGs)
- Add `numpy` + `opencv-python-headless` to manifest (HA already ships numpy; opencv adds ~30 MB)

Lean toward the vendored single-file approach if we can get it down to a few hundred lines.

## Endpoint map: what we do now vs what we'd do

Both surfaces hit the **same host** (`monitoring-eu.epcube.com`), just different path prefixes and auth schemes.

| Today (`/v1/api/home/*` + JSESSIONID cookie) | Mobile (`/api/device/*` + Bearer header) | Notes |
|---|---|---|
| `GET /v1/api/home/homeDeviceInfo?sgSn=` | `GET /api/device/homeDeviceInfo?dayMonthYearFormat=YYYY-MM-DD&sgSn=` | Same response shape, mobile path adds a date query param |
| `GET /v1/api/home/getSwitchMode?devId=` | `GET /api/device/getSwitchMode?devId=` | Identical |
| `GET /v1/api/home/deviceList` | `GET /api/device/deviceList` | Identical |
| `GET /v1/api/home/getSellingConfig/{devId}` | _unknown — needs capture_ | Check before refactor |
| `POST /v1/api/home/setSelfConsumption` | `POST /api/device/switchMode` (workStatus=1) | **Three endpoints collapse into one** |
| `POST /v1/api/home/setBackUp` | `POST /api/device/switchMode` (workStatus=3) | |
| `POST /v1/api/home/setTimOfUse` | `POST /api/device/switchMode` (workStatus=2, onlySave=0) | `onlySave=1` saves TOU without switching modes — handy for shim |
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

## The risk we have to plan for: write-revocation on mobile-app open

From [Bobsilvio/epcube#24 comments](https://github.com/Bobsilvio/epcube/issues/24):

> "If the EP Cube server detects a secondary login (like opening the official mobile app), it instantly revokes 'Write' privileges for the Home Assistant token, but allows 'Read' privileges to continue working. This means sensor data keeps updating, but mode changes fail. Furthermore, simply clicking 'Reload' on the integration in Home Assistant does not clear the cached token. To properly test a fresh token, the integration must be completely deleted and re-added."

**Why this matters specifically for us**: Predbat issues overrides via the shim throughout the day. If the user opens the mobile app to glance at SoC, the shim's *next* write silently 403s — but the read side keeps working, so our coordinator polling shows fine, and Predbat thinks the cloud is in the override state when the cloud actually isn't. Sticky drift between planned and actual.

**Required mitigations** (none are optional for production):

1. **Post-write read-back** in [services.py](../custom_components/ep_cube/services.py) — after every `switchMode` POST, re-read `getSwitchMode` once and verify `workStatus` matches the intended target. If it doesn't, treat as a write-failure and surface to the user.
2. **Auto re-auth on write 403** — store the password (or a derived encrypted form) at config-flow time so the integration can run the 3-step captcha+login flow when writes start failing.
3. **HA notification** when re-auth fires or repeatedly fails — user needs to know if the mobile app is squatting on the write privilege.
4. **Predbat-side back-off** — if writes have failed N times in a row, the shim should set its `Predbat Active` state to a degraded value so Predbat itself stops issuing further overrides until the situation is resolved.

This is more work than just swapping the auth header. But the alternative (writes-failing-silently-until-user-notices) is unacceptable for an automation that controls a £15k battery.

## Open questions (to resolve before code starts)

1. **Is `getSellingConfig` available on `/api/device/*`?** Needs a single curl-equivalent capture from a logged-in mobile session. If not, we keep that one endpoint on the JSESSIONID path (hybrid auth — ugly but workable), or accept losing the sellingPowerLimit/grid-code read.
2. **What's the actual 401/403/expiry behaviour?** Need to:
   - Note the timestamp on a fresh token, leave it alone for 24h, retry — does it 401?
   - Try to deliberately invalidate by opening the mobile app, then probe a read and a write — confirm reads keep working and writes 403.
   - Document the response body of a *genuine* write-revocation (vs the misleading 403 from typing errors).
3. **Can captcha-solving be done with numpy alone?** Worth a 30-minute spike — if yes, no manifest change. If no, accept opencv-headless dependency.
4. **Token storage strategy in HA**: store password in `entry.data` (encrypted at rest by HA) and re-auth on demand, or store token + refresh on 403 by re-prompting user? Decision affects UX significantly.

## Recommendation

**Proceed to implementation in Phase 3.2, sequenced as:**

1. **Capture session** (~30 min) — log into [epcube-token.streamlit.app](https://epcube-token.streamlit.app/) with our credentials, get a token, then curl-spike the 4 read endpoints + 1 write endpoint to confirm response shapes against our schemas. Validate `getSellingConfig` availability on `/api/device/*`. Test write-revocation behaviour by opening mobile app mid-session.
2. **Vendor the captcha-solver** as a self-contained module (numpy-only ideally) — `custom_components/ep_cube/captcha.py`.
3. **Refactor `api.py`** — new `_request` adds `Authorization` header instead of `Cookie`; collapse three write methods into one `switch_mode(...)`; add post-write read-back helper.
4. **Refactor `config_flow.py`** — replace cookie field with email + password fields; run the 3-step flow on submit; store password encrypted; bootstrap initial token.
5. **Add re-auth on 403** — at the `_request` layer, catch 403/401 once, re-run login, retry.
6. **Update mock server** to mirror `/api/device/*` + `/api/open/common/*` endpoints so we keep regression coverage.
7. **Document the write-revocation behaviour** in [TROUBLESHOOTING.md](TROUBLESHOOTING.md) prominently.

**Defer until after**: Phase 3.2 should *not* block Phase 4 (HACS) — but should land *before* HACS publication, since the JSESSIONID UX is a hard barrier for new users.

## References

- [Bobsilvio/epcube](https://github.com/Bobsilvio/epcube) — the integration we're learning from
- [Bobsilvio/epcube-token](https://github.com/Bobsilvio/epcube-token) — the captcha-solving Streamlit tool ([live](https://epcube-token.streamlit.app/))
- [Issue #24 in epcube](https://github.com/Bobsilvio/epcube/issues/24) — the strict-typing bug-fix saga + write-revocation note
- Our current contract reference: `<captures-private>/2026-05-20-contract-extract.md` (web-portal surface only — does **not** cover `/api/device/*`)
