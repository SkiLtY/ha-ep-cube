/**
 * EP Cube — TOU Schedule Editor card
 *
 * Phase 4.1 — companion to the ep_cube.set_tou_schedule service.
 *
 * Lets the user edit the cube's workday + weekend non-DST tier lists
 * (peak / mid-peak / off-peak) and hit Save to write them via the
 * service in one round-trip. DST tier lists, day masks, reserves, and
 * per-tier prices are preserved server-side from the cube's current
 * state.
 *
 * Installation (HA OS / Container):
 *   1. Copy this file to /config/www/ep-cube-tou-editor.js
 *   2. Add the JS to Lovelace resources:
 *        Settings → Dashboards → Resources → Add Resource
 *        URL:  /local/ep-cube-tou-editor.js
 *        Type: JavaScript module
 *   3. Add the card to a dashboard:
 *        type: custom:ep-cube-tou-editor
 *
 * Configuration:
 *   type: custom:ep-cube-tou-editor
 *   device_id: "5613"       # optional — only needed if multiple cubes configured
 *   title: "TOU Schedule"   # optional, default "TOU schedule"
 *
 * No external dependencies — uses Lit primitives bundled with HA's frontend.
 */
const LitElement = customElements.get("ha-panel-lovelace")
  ? Object.getPrototypeOf(customElements.get("ha-panel-lovelace"))
  : Object.getPrototypeOf(customElements.get("hui-masonry-view") || customElements.get("hc-launcher"));
const html = LitElement.prototype.html;
const css = LitElement.prototype.css;

const TIERS = [
  { key: "peak", label: "Peak", colour: "var(--error-color, #db4437)" },
  { key: "mid_peak", label: "Mid-peak", colour: "var(--warning-color, #ffa600)" },
  { key: "off_peak", label: "Off-peak", colour: "var(--success-color, #43a047)" },
];

const SLOT_RE = /^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$/;

const blankSchedule = () => ({
  workday: { peak: [], mid_peak: [], off_peak: [] },
  weekend: { peak: [], mid_peak: [], off_peak: [] },
});

// Parse "HH:MM-HH:MM" → { start: "HH:MM", end: "HH:MM" } | null
function splitSlot(s) {
  const m = SLOT_RE.exec(s || "");
  if (!m) return null;
  return { start: `${m[1]}:${m[2]}`, end: `${m[3]}:${m[4]}` };
}

// Parse a wire-format slot "HH:MM_HH:MM_PRICE" into user form "HH:MM-HH:MM"
// (price is preserved server-side, not surfaced in the UI for MVP).
function wireToUser(slot) {
  const parts = String(slot).split("_");
  if (parts.length < 2) return "";
  return `${parts[0]}-${parts[1]}`;
}

function minutesOfDay(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// Run the same validation rules as the backend service so the user sees
// errors inline rather than waiting for the service call to fail.
function validateDay(tiers) {
  const errors = [];
  const parsedByTier = {};
  for (const tier of TIERS) {
    const parsed = [];
    for (const slot of tiers[tier.key] || []) {
      const split = splitSlot(slot);
      if (!split) {
        errors.push(`${tier.label}: "${slot}" must be HH:MM-HH:MM`);
        continue;
      }
      const start = minutesOfDay(split.start);
      const end = minutesOfDay(split.end);
      if (end <= start) {
        errors.push(`${tier.label}: ${slot} ends before it starts`);
        continue;
      }
      parsed.push({ start, end, tierLabel: tier.label });
    }
    // Within-tier overlap.
    const sorted = [...parsed].sort((a, b) => a.start - b.start);
    for (let i = 1; i < sorted.length; i++) {
      if (sorted[i].start < sorted[i - 1].end) {
        errors.push(`${tier.label}: slots overlap`);
        break;
      }
    }
    parsedByTier[tier.key] = parsed;
  }
  // Cross-tier overlap (minute coverage map; max 1440 minutes).
  const coverage = new Map();
  for (const tier of TIERS) {
    for (const slot of parsedByTier[tier.key] || []) {
      for (let minute = slot.start; minute < slot.end; minute++) {
        const claimed = coverage.get(minute);
        if (claimed && claimed !== tier.label) {
          const hh = String(Math.floor(minute / 60)).padStart(2, "0");
          const mm = String(minute % 60).padStart(2, "0");
          errors.push(`${tier.label} overlaps ${claimed} at ${hh}:${mm}`);
          return errors;
        }
        coverage.set(minute, tier.label);
      }
    }
  }
  return errors;
}

class EpCubeTouEditor extends LitElement {
  static get properties() {
    return {
      hass: {},
      _config: { state: true },
      _activeProfile: { state: true },
      _schedule: { state: true },
      _switchToTou: { state: true },
      _errors: { state: true },
      _saving: { state: true },
      _statusMsg: { state: true },
    };
  }

  constructor() {
    super();
    this._activeProfile = "workday";
    this._schedule = blankSchedule();
    this._switchToTou = false;
    this._errors = [];
    this._saving = false;
    this._statusMsg = "";
    this._hydrated = false;
  }

  setConfig(config) {
    this._config = { title: "TOU schedule", ...config };
  }

  getCardSize() {
    return 6;
  }

  // Hydrate the form from the current operating-mode select entity's
  // matching device, by pulling the cube's stored tier lists from the
  // getSwitchMode response — but that lives behind the integration and
  // isn't exposed to the frontend directly. For MVP we hydrate from
  // user-edits only and let the cube's existing schedule survive
  // untouched until they explicitly save.
  //
  // (Future: surface a "Reload from cube" button that calls a read-only
  // WebSocket command. Out of scope for v0.6.0.)

  _onSlotChange(profile, tier, idx, field, value) {
    const slots = [...(this._schedule[profile][tier] || [])];
    const current = splitSlot(slots[idx] || "00:00-00:00") || { start: "00:00", end: "00:00" };
    current[field] = value;
    slots[idx] = `${current.start}-${current.end}`;
    this._schedule = {
      ...this._schedule,
      [profile]: { ...this._schedule[profile], [tier]: slots },
    };
    this._statusMsg = "";
  }

  _addSlot(profile, tier) {
    const slots = [...(this._schedule[profile][tier] || []), "00:00-00:00"];
    this._schedule = {
      ...this._schedule,
      [profile]: { ...this._schedule[profile], [tier]: slots },
    };
    this._statusMsg = "";
  }

  _removeSlot(profile, tier, idx) {
    const slots = [...(this._schedule[profile][tier] || [])];
    slots.splice(idx, 1);
    this._schedule = {
      ...this._schedule,
      [profile]: { ...this._schedule[profile], [tier]: slots },
    };
    this._statusMsg = "";
  }

  async _onSave() {
    const errors = [
      ...validateDay(this._schedule.workday).map((e) => `Workday: ${e}`),
      ...validateDay(this._schedule.weekend).map((e) => `Weekend: ${e}`),
    ];
    this._errors = errors;
    if (errors.length) {
      this._statusMsg = "";
      return;
    }
    this._saving = true;
    this._statusMsg = "Saving…";
    try {
      const data = {
        peak_workday: this._schedule.workday.peak,
        mid_peak_workday: this._schedule.workday.mid_peak,
        off_peak_workday: this._schedule.workday.off_peak,
        peak_weekend: this._schedule.weekend.peak,
        mid_peak_weekend: this._schedule.weekend.mid_peak,
        off_peak_weekend: this._schedule.weekend.off_peak,
        switch_to_tou: this._switchToTou,
      };
      if (this._config?.device_id) data.device_id = this._config.device_id;
      await this.hass.callService("ep_cube", "set_tou_schedule", data);
      this._statusMsg = "Schedule saved.";
    } catch (err) {
      this._statusMsg = `Save failed: ${err?.message || err}`;
    } finally {
      this._saving = false;
    }
  }

  _renderSlotRow(profile, tier, idx, slot) {
    const split = splitSlot(slot) || { start: "00:00", end: "00:00" };
    return html`
      <div class="slot-row">
        <input
          type="time"
          .value=${split.start}
          @change=${(e) => this._onSlotChange(profile, tier, idx, "start", e.target.value)}
        />
        <span class="dash">–</span>
        <input
          type="time"
          .value=${split.end}
          @change=${(e) => this._onSlotChange(profile, tier, idx, "end", e.target.value)}
        />
        <button
          class="remove"
          title="Remove slot"
          @click=${() => this._removeSlot(profile, tier, idx)}
        >
          ✕
        </button>
      </div>
    `;
  }

  _renderTier(profile, tier) {
    const slots = this._schedule[profile][tier.key] || [];
    return html`
      <div class="tier">
        <div class="tier-header" style="--tier-colour: ${tier.colour}">
          <span class="tier-dot"></span>
          <span class="tier-label">${tier.label}</span>
          <button class="add" @click=${() => this._addSlot(profile, tier.key)}>
            + Add slot
          </button>
        </div>
        ${slots.length === 0
          ? html`<div class="empty">No slots configured.</div>`
          : slots.map((s, i) => this._renderSlotRow(profile, tier.key, i, s))}
      </div>
    `;
  }

  render() {
    if (!this._config) return html``;
    const profile = this._activeProfile;
    return html`
      <ha-card .header=${this._config.title}>
        <div class="card-body">
          <div class="tabs">
            <button
              class=${profile === "workday" ? "tab active" : "tab"}
              @click=${() => (this._activeProfile = "workday")}
            >
              Workday
            </button>
            <button
              class=${profile === "weekend" ? "tab active" : "tab"}
              @click=${() => (this._activeProfile = "weekend")}
            >
              Weekend
            </button>
          </div>

          ${TIERS.map((t) => this._renderTier(profile, t))}

          <div class="footer">
            <label class="switch-tou">
              <input
                type="checkbox"
                .checked=${this._switchToTou}
                @change=${(e) => (this._switchToTou = e.target.checked)}
              />
              Switch the cube into Time-of-Use mode when saving
            </label>

            ${this._errors.length
              ? html`<ul class="errors">
                  ${this._errors.map((e) => html`<li>${e}</li>`)}
                </ul>`
              : ""}

            <div class="save-row">
              <button class="save" ?disabled=${this._saving} @click=${this._onSave}>
                ${this._saving ? "Saving…" : "Save schedule"}
              </button>
              <span class="status">${this._statusMsg}</span>
            </div>

            <div class="hint">
              DST tier lists, prices and reserves are preserved from the
              cube's current state. Empty tier = cleared on save.
            </div>
          </div>
        </div>
      </ha-card>
    `;
  }

  static get styles() {
    return css`
      .card-body {
        padding: 0 16px 16px 16px;
      }
      .tabs {
        display: flex;
        gap: 8px;
        margin: 8px 0 16px 0;
      }
      .tab {
        flex: 1;
        padding: 8px 12px;
        border: 1px solid var(--divider-color, #444);
        background: var(--card-background-color, #1c1c1c);
        color: var(--primary-text-color);
        border-radius: 8px;
        cursor: pointer;
      }
      .tab.active {
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
        border-color: var(--primary-color);
      }
      .tier {
        margin-bottom: 16px;
        border: 1px solid var(--divider-color, #333);
        border-radius: 8px;
        padding: 8px 12px;
      }
      .tier-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
      }
      .tier-dot {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: var(--tier-colour);
      }
      .tier-label {
        flex: 1;
        font-weight: 600;
      }
      .add {
        background: transparent;
        border: 1px solid var(--divider-color, #444);
        color: var(--primary-text-color);
        border-radius: 6px;
        padding: 4px 8px;
        cursor: pointer;
      }
      .slot-row {
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 4px 0;
      }
      .slot-row input[type="time"] {
        padding: 4px 8px;
        background: var(--card-background-color, #1c1c1c);
        color: var(--primary-text-color);
        border: 1px solid var(--divider-color, #444);
        border-radius: 6px;
        font: inherit;
      }
      .dash {
        opacity: 0.6;
      }
      .remove {
        background: transparent;
        border: 1px solid var(--divider-color, #444);
        color: var(--error-color, #db4437);
        border-radius: 6px;
        padding: 2px 8px;
        cursor: pointer;
      }
      .empty {
        opacity: 0.6;
        font-size: 0.9em;
        padding: 4px 0;
      }
      .footer {
        margin-top: 16px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .switch-tou {
        display: flex;
        align-items: center;
        gap: 8px;
        cursor: pointer;
      }
      .errors {
        background: rgba(219, 68, 55, 0.1);
        border-left: 3px solid var(--error-color, #db4437);
        margin: 4px 0;
        padding: 8px 24px;
        list-style: disc;
      }
      .save-row {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .save {
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
        border: none;
        border-radius: 8px;
        padding: 10px 20px;
        cursor: pointer;
        font-size: 1em;
      }
      .save:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      .status {
        opacity: 0.8;
        font-size: 0.95em;
      }
      .hint {
        margin-top: 8px;
        font-size: 0.85em;
        opacity: 0.6;
        line-height: 1.4;
      }
    `;
  }
}

if (!customElements.get("ep-cube-tou-editor")) {
  customElements.define("ep-cube-tou-editor", EpCubeTouEditor);
}

// Register with HA's custom-card catalogue so it shows up in the
// dashboard card picker.
window.customCards = window.customCards || [];
window.customCards.push({
  type: "ep-cube-tou-editor",
  name: "EP Cube — TOU Schedule Editor",
  description:
    "Edit the cube's workday + weekend Time-of-Use tier lists. Writes via ep_cube.set_tou_schedule.",
});
