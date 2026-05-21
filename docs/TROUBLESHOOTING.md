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

## When in doubt

Before debugging an external dep (Predbat, AppDaemon, HA itself), **check its upstream install docs first**. Phase 2b's single biggest time-sink was not doing this — Predbat's install path had been deprecated and we wasted hours on the old route.
