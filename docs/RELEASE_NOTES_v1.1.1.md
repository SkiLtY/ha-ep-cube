## 🪜 EP Cube Integration v1.1.1 — Stale-Entity Cleanup

> **TL;DR** — small patch on top of v1.1.0. The two sensors deleted in v1.1.0 (`grid_today` + `nonbackup_today`) were leaving behind `unavailable` ghost rows in the HA entity registry until the user manually deleted them. This release sweeps those rows on every setup. Upgrade and the ghosts disappear after the next HA restart. No behavioural change otherwise.

### 🧹 What's fixed

After upgrading to v1.1.0, users would see two leftover entities in HA's UI:

- `sensor.ep_cube_grid_today` (or whatever name your HA had localised it to)
- `sensor.ep_cube_non_backup_loads_today`

Both stuck on `unavailable` because the integration no longer registers them. v1.1.1 adds a one-time sweep on every config-entry setup that removes any registry row whose unique_id matches the deleted set:

- `<entry_id>_grid_today` (deleted v1.1.0 — direction-ambiguous)
- `<entry_id>_nonbackup_today` (deleted v1.1.0 — zero on EU firmware)

Scoped to the config entry only, so a multi-entry / multi-cube setup where one entry was upgraded ahead of others doesn't yank entries from siblings. Idempotent — running against an already-clean registry is a no-op.

### 🧪 Tests

Suite grows 176 → 178 cases. 2 new in `tests/test_setup.py::TestStaleEntityPurge`:

- `test_pre_existing_grid_today_entry_purged_on_setup` — seeds the registry with both ghost rows, runs setup, asserts both are gone afterwards
- `test_purge_is_idempotent_when_registry_clean` — fresh install with no ghosts; setup still succeeds, no-op purge

All 178 green on Python 3.12.

### 📦 Upgrading

- **HACS users**: bump v1.1.0 → v1.1.1. **Restart HA** — that's when the sweep runs. Refresh the *Devices & services* page and the ghost rows are gone.
- **Manual users**: re-copy `custom_components/ep_cube/` from the release zip. Restart HA.

### 🛣 What's next

Roadmap unchanged from v1.1.0:
- **v1.2** — cube-native monthly + annual rollups (Phase 4.2 Tier 3)
- **v1.3** — lifetime totals (Phase 4.2 Tier 4) + eco metrics (Tier 5)
- **HACS Default submission** — held until v1.1 has bedded in

### ☕ Support

If this saves you the hours, consider [tossing a ko-fi in the tank](https://ko-fi.com/SkiLtY). Thanks!

---

*Not affiliated with or endorsed by Canadian Solar or EP Cube.*
