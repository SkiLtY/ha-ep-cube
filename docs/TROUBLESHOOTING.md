# Troubleshooting

Known gotchas hit during bring-up and dev. Grouped by area.

## HA integration

| Symptom | Cause | Fix |
|---|---|---|
| HA integration form rejects with `cannot_connect` | `https://mock:8765` (default before fix) — mock is plain HTTP | Use `http://mock:8765`. Default in `const.py` is now correct. |
| Only 3 of 9 sensors visible after first install | Missing `translations/en.json` and no `DeviceInfo` — entities fell back to device-class labels | Both fixed in commit `911f584`. Delete + re-add integration to refresh entity registry. |
| HA service call returns `Unknown error` after `charge_start` | Shim's `get_tou_schedule()` 405s because mock wasn't rebuilt | `docker compose up -d --build mock` (not `restart`). |

## Synology / deploy

| Symptom | Cause | Fix |
|---|---|---|
| `Bind mount failed: '/volume1/docker/ha-ep-cube/ha_config' does not exist` | `.gitignore` had `ha_config/` (trailing slash) which excluded the placeholder `.gitkeep` | Fixed in `4acd66a` — pattern is now `ha_config/*` + `!ha_config/.gitkeep`. |
| `subsystem request failed on channel 0` when scp'ing to Synology | DSM SSH server doesn't enable SFTP subsystem | Use `scp -O ...` to force legacy SCP protocol. Or pipe via ssh: `Get-Content key -Raw \| ssh host 'cat > /path'`. |
| `fatal: detected dubious ownership` on Synology after `git init` | Volume owned by <user> but git context confused | `git config --global --add safe.directory /volume1/docker/ha-ep-cube` (must be `--global`, not `--add` alone — chicken/egg). |
| `git clone . .` fails with `not empty directory` | The `.ssh/` dir we created counts as content | Use in-place init: `git init && git remote add origin ... && git fetch && git checkout -b main --track origin/main`. |
| Windows OpenSSH scp doesn't expand `~` | Microsoft port limitation — `~` works in PowerShell cmdlets but not external `.exe` arguments | Use `$HOME\.ssh\...` in PowerShell, `%USERPROFILE%\.ssh\...` in cmd. |
| Synology `sudo` prompts for password in non-interactive ssh | sudo needs a TTY without `-S` or askpass helper | Add user to `docker` group (`sudo synogroup --member docker <user>`) — eliminates sudo for docker. Or run scheduled tasks as root in DSM. |

## Predbat

| Symptom | Cause | Fix |
|---|---|---|
| HA Container has no Add-on store | Add-ons require Supervisor (only on HA OS / HA Supervised) | Run Predbat via `nipar44/predbat_addon` — see [PREDBAT.md](PREDBAT.md). |
| Trying to install Predbat under stock AppDaemon hits a wall — `Hass.__init__()` signature mismatch, `graphlib.CycleError`, numpy/aiohttp version churn | Predbat upstream **retired the AppDaemon install path**; current Predbat source is wired for the new "Predbat app" runtime contract | Don't fight it — use `nipar44/predbat_addon:alpine-latest`. (We tried 4.4.2 + 4.5.x + `python:3.11-slim` custom image before discovering this; all dead ends.) |
| `Inverter type EP_CUBE not defined` | Predbat doesn't recognise unknown `inverter_type` values unless you supply the capability dict yourself | In `predbat_config/apps.yaml` add a top-level `inverter:` block under `pred_bat:` with `has_service_api: True`, `output_charge_control: "power"`, etc. Use the "I want to add an unsupported inverter" template at the bottom of Predbat's `inverter-setup.md`. |
| Predbat's `*_service` calls fail with `required key not provided @ data['end_time']` | Predbat passes empty `data {}` on every service call — it writes the actual params to its own dummy entities (`sensor.predbat_EP_CUBE_0_charge_start_time` etc.) and expects the integration to read those. Our shim's `services.yaml` (Phase 2a) required the args inline. | Phase 2b.1 rework — read params from Predbat's entities inside `handle_charge_start`/etc.; make the args optional in `services.yaml`. |
| Predbat: `Error: You have not set load_today or load_forecast in apps.yaml` | Predbat hard-requires a daily-cumulative-kWh load sensor; our integration only exposes instantaneous `load_power` (W) | Create two HA helpers (UI → Settings → Helpers): a Riemann-sum integral of `sensor.ep_cube_<dev>_load_power` (`k` prefix, hours), then a Utility Meter with daily reset on top. Point `load_today:` at the Utility Meter entity. |

## EP Cube cloud API (mobile-app surface, post Phase 3.2)

Wire-level gotchas discovered during the 2026-05-21 bring-up of the
captcha-solver login + `/api/device/*` Bearer surface. Full context in
[PHASE_3_2.md](PHASE_3_2.md); this is the symptom → cause → fix table.

| Symptom | Cause | Fix |
|---|---|---|
| HA shows `solar_power: 64000 W` while EP Cube app shows 0.63 kW for same poll | Mobile-app `/api/device/homeDeviceInfo` returns power as **centi-kilowatt integers** (0.01 kW units), not W and not kW. Took 3 wrong guesses (×1000 → ×1 → ×10) to land on the right multiplier. | `_power_to_w(v) = float(v) * 10` in `api.py`. Wire `64` = 0.64 kW = 640 W. kWh fields stay as plain floats. |
| Every `homeDeviceInfo` poll throws `inner envelope error status=1` after the api.py rewrite | Over-eager inner-envelope check: I assumed any `data.status` was an HTTP-shaped error code. Reality: on `homeDeviceInfo` the field is the **device-online flag** (`"1"` = online), not an envelope error. | Only flag `inner_code >= 400` — see `api.py::_request` comment. Domain field collisions like this are why we narrowed the check from "any non-200 inner status" to "4xx/5xx shape". |
| `/api/open/common/login` returns `500 "captcha is overdue, please try again later."` despite a fresh captcha solve | Wrong `captchaVerification` construction. It is NOT `f"{token}---{pointJson}"` (where pointJson is the already-encrypted string sent to `/check`). | Real format: `base64(AES_ECB(secret_key, f"{token}---{json({x,y})}"))` — AES-encrypt the **literal `token---plaintext_json` string** with the per-session `secret_key`. Same `secret_key` used for both pointJson and captchaVerification. Reference: Bobsilvio/epcube-token `generate_captcha_verification`. |
| Captcha solver runs but every `/login` says "overdue" — yet `/captcha/check` returned `message:"Success"` | The outer envelope `message:"Success"` is **envelope-level only** — it just means the HTTP envelope reached the captcha service. The real verdict lives in `data.repCode` (`"0000"` success / `"6111"` failure) or `data.repData.result` (`true`). | Check `data.repCode == "0000"` or `data.repData.result is True`. **Do NOT add a `message == "Success"` fallback** — it's exactly the false-positive that lets failed captchas proceed to `/login`. |
| 100% captcha-solve failure rate (vs Bobsilvio's reported ~80%) with bit-exact OpenCV port | Textbook `Image.convert("L")` on the cube's palette-mode background PNG destroys the match — palette indices, not RGB luminance, are what cross-correlate with the puzzle's signal. Mean luminance ~142 with `convert("L")`, ~44 with raw palette indices. | In `_decode_b64_image_grey`, return raw `np.array(pil)` for palette mode without conversion. The textbook-correct grayscale path is wrong here. |
| `switchMode` returns `403 "token expired"` immediately after a fresh login | Day arrays (`activeWeek`, `dayLightActiveWeek*`) sent as `list[int]` get rejected with a misleading 403. The 403 is a payload-typing error, not auth. | Coerce day arrays to `list[str]` on write (`["1","2","3","4","5"]`). Cube normalises them back to `list[int]` on read — `_values_equal` accepts both. |
| `switchMode` returns `500 "The parameter cannot be null ：weatherWatch"` (note fullwidth `：`) | Cloud is China-hosted; validation errors come back as 500 with the field named. Minimal 3-field calls don't work — every field in `build_switch_mode_payload` is required. | Always build the full payload via `build_switch_mode_payload` or `payload_from_switch_mode_read`. The 500/fullwidth-colon shape is also useful for error-discrimination: a genuine 403 now reliably means "token rejected", not "payload wrong". |
| Cube silently re-enables weather-based pre-charging, fighting Predbat's economic plan | `weatherWatch` is persistent state on the cube — if you omit it, the prior value sticks. Similarly `onlySave` (save-only mode). | Force `weatherWatch:"0"` and `onlySave:"0"` on every write. `build_switch_mode_payload` does this unconditionally. |
| HA restart triggers a fresh captcha solve every time | Reauth callback returned a token but didn't persist it back to the config entry — next HA boot started with the original (now expired) token. | In `__init__.py`, wrap `captcha.make_reauth_callback` in a closure that writes the refreshed token back via `hass.config_entries.async_update_entry(entry, data={...})`. |

## When in doubt

Before debugging an external dep (Predbat, AppDaemon, HA itself), **check its upstream install docs first**. Phase 2b's single biggest time-sink was not doing this — Predbat's install path had been deprecated and we wasted hours on the old route.
