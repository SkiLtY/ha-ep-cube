"""HA Repair flow — Predbat-priority enforcement.

v1.0 pivot: the integration's mission is "let Predbat control the EP
Cube". Manually painted TOU slots on the cube create state divergence
(Predbat re-plans against a model that doesn't reflect what the cube
is actually doing). When we detect that condition we raise an HA Repair
issue offering to wipe the user-painted slots and hand control back to
Predbat.

Detection
---------
Runs from the coordinator after every successful poll. The issue is
raised when BOTH of these are true:

1. The cube's switch-mode snapshot has at least one non-shim slot in any
   of the six non-DST tier lists (workday + weekend × peak / mid_peak /
   off_peak). Shim-signature slots are ignored — they're Predbat's own
   in-flight overrides and don't conflict with anything.
2. At least one entity in the `predbat.*` domain exists in HA. That's
   our best signal that the user actually runs Predbat — otherwise the
   manual slots are deliberate and the integration should leave them
   alone.

When either condition clears the issue is auto-deleted. The detection
helper is idempotent against the issue registry, so calling it on every
30s poll is safe.

Fix flow
--------
Single confirm step. On submit:

  1. If a shim override is in flight, abandon it (the wipe would clobber
     the override's revert anyway).
  2. Read the cube's current state.
  3. Build a payload that wipes the six non-DST tier lists to empty.
     DST tier lists, reserves, day masks and per-tier prices are left
     alone — they're not what's conflicting with Predbat.
  4. Use the 2-write dance when the cube is in non-TOU mode: write A
     flips workStatus → TOU and applies the empty tier lists (TOU →
     TOU never drops the schedule write); write B flips workStatus
     back to the original mode. Pattern + provenance documented in
     services.py's PredbatShim and ARCHITECTURE.md.
  5. Force a coordinator refresh — the detection helper will run on
     the refreshed snapshot and (assuming the wipe succeeded) tear
     down the issue.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .api import EPCubeError, payload_from_switch_mode_read
from .const import DOMAIN, WORK_STATUS_TOU
from .services import (
    _USER_TIER_LIST_KEYS,
    _strip_shim_slots,
    has_non_shim_user_slots,
)

_LOGGER = logging.getLogger(__name__)

ISSUE_PREDBAT_PRIORITY = "predbat_priority"


def _issue_id(entry_id: str) -> str:
    return f"{ISSUE_PREDBAT_PRIORITY}_{entry_id}"


def _predbat_entities_present(hass: HomeAssistant) -> bool:
    return any(
        state.entity_id.startswith("predbat.") for state in hass.states.async_all()
    )


def evaluate_predbat_priority_issue(
    hass: HomeAssistant,
    entry_id: str,
    switch_mode: dict[str, Any] | None,
) -> None:
    """Idempotent: raise or clear the Predbat-priority issue for this entry.

    Called from the coordinator after every successful poll. Cheap — a
    single scan over six lists + a domain-prefix check on hass.states.
    """
    iid = _issue_id(entry_id)
    should_raise = (
        switch_mode is not None
        and has_non_shim_user_slots(switch_mode)
        and _predbat_entities_present(hass)
    )
    if should_raise:
        ir.async_create_issue(
            hass,
            DOMAIN,
            iid,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_PREDBAT_PRIORITY,
            data={"entry_id": entry_id},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, iid)


class PredbatPriorityRepairFlow(RepairsFlow):
    """Single-step confirm flow that wipes the cube's non-DST tier lists."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        if user_input is not None:
            await self._wipe_user_slots()
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))

    async def _wipe_user_slots(self) -> None:
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if data is None:
            raise HomeAssistantError(
                f"EP Cube entry {self._entry_id} is no longer loaded — "
                "reload the integration and re-run the repair"
            )
        client = data["client"]
        coordinator = data["coordinator"]
        shim = data["shim"]

        if shim.is_active:
            _LOGGER.info(
                "predbat_priority repair: abandoning active shim override before wipe"
            )
            shim.abandon_override()

        try:
            live = await client.get_switch_mode()
        except EPCubeError as err:
            raise HomeAssistantError(f"cannot read current cube state: {err}") from err

        live_clean, _ = _strip_shim_slots(live)
        wipe_overrides: dict[str, Any] = {key: [] for key in _USER_TIER_LIST_KEYS}

        current_work_status = str(live_clean.get("workStatus") or "1")
        # 2-write dance for the same reason as PredbatShim:
        #   - TOU → non-TOU in a single write: cube adopts the new mode but
        #     drops the tier-list portion.
        #   - non-TOU → non-TOU (no mode transition): cube ignores tier-list
        #     writes entirely.
        # So when the cube is in non-TOU mode, flip via TOU as an intermediate
        # state, then return to the user's original mode.
        needs_two_write = current_work_status != WORK_STATUS_TOU

        payload_a = payload_from_switch_mode_read(
            client.dev_id,
            live_clean,
            overrides=(
                {**wipe_overrides, "workStatus": WORK_STATUS_TOU}
                if needs_two_write
                else wipe_overrides
            ),
        )
        try:
            await client.switch_mode(payload_a)
        except EPCubeError as err:
            raise HomeAssistantError(
                f"cube rejected tier-list wipe (write A): {err}"
            ) from err

        if needs_two_write:
            payload_b = payload_from_switch_mode_read(
                client.dev_id,
                live_clean,
                overrides={**wipe_overrides, "workStatus": current_work_status},
            )
            try:
                await client.switch_mode(payload_b)
            except EPCubeError as err:
                raise HomeAssistantError(
                    "schedule wiped but mode restore failed — cube is stuck "
                    f"in TOU instead of original mode {current_work_status}: {err}"
                ) from err

        _LOGGER.info(
            "predbat_priority repair: wiped 6 non-DST tier lists "
            "(2-write=%s, original_mode=%s)",
            needs_two_write,
            current_work_status,
        )

        await coordinator.async_request_refresh()


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """HA Repairs platform entry point."""
    entry_id = (data or {}).get("entry_id")
    if not entry_id:
        raise HomeAssistantError(
            f"missing entry_id in repair issue {issue_id} — cannot dispatch fix flow"
        )
    return PredbatPriorityRepairFlow(entry_id=str(entry_id))
