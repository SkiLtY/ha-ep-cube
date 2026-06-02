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
  // `defaultPrice` shown as input placeholder when the tier has no current
  // price on the cube. Matches DEFAULT_TIER_PRICE_* in const.py — kept in
  // sync manually since the card has no Python import. p/kWh.
  { key: "peak",     label: "Peak",     colour: "var(--error-color, #db4437)",   defaultPrice: 0.40 },
  { key: "mid_peak", label: "Mid-peak", colour: "var(--warning-color, #ffa600)", defaultPrice: 0.25 },
  { key: "off_peak", label: "Off-peak", colour: "var(--success-color, #43a047)", defaultPrice: 0.05 },
];

const SLOT_RE = /^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$/;

// Per-tier price range — must match _PRICES_SCHEMA in services.py.
const PRICE_MIN = 0.0;
const PRICE_MAX = 999.99;

const blankSchedule = () => ({
  workday: { peak: [], mid_peak: [], off_peak: [] },
  weekend: { peak: [], mid_peak: [], off_peak: [] },
});

// Mirror of `parse_tou_prices` in services.py. null = "no current price on
// the cube" (tier empty or all shim slots), so the input shows a placeholder.
const blankPrices = () => ({
  workday: { peak: null, mid_peak: null, off_peak: null },
  weekend: { peak: null, mid_peak: null, off_peak: null },
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

function minutesToHHMM(mins) {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
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
        // The cube (and EP Cube mobile app) doesn't support midnight-crossing
        // slots — wire format treats end <= start as invalid. Point users at
        // the 23:59 escape hatch.
        errors.push(
          `${tier.label}: ${slot} ends before it starts — ` +
          `use 23:59 to end a slot at midnight`,
        );
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
      _prices: { state: true },
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
    this._prices = blankPrices();
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

  // Hydrate from the operating-mode select entity's `tou_schedule`
  // attribute (added in v0.6.2 — the coordinator polls getSwitchMode and
  // the select exposes the parsed workday/weekend tier lists).
  //
  // Auto-hydrate runs once on first hass binding so we don't clobber the
  // user's in-progress edits on every coordinator tick. The "Reload from
  // cube" button forces a re-hydrate.
  updated(changed) {
    super.updated?.(changed);
    if (!this._hydrated && this.hass) {
      this._hydrateFromCube();
    }
  }

  _readCubeSchedule() {
    const state = this.hass?.states?.["select.ep_cube_operating_mode"];
    const sched = state?.attributes?.tou_schedule;
    if (!sched || typeof sched !== "object") return null;
    return {
      workday: {
        peak: [...(sched.workday?.peak || [])],
        mid_peak: [...(sched.workday?.mid_peak || [])],
        off_peak: [...(sched.workday?.off_peak || [])],
      },
      weekend: {
        peak: [...(sched.weekend?.peak || [])],
        mid_peak: [...(sched.weekend?.mid_peak || [])],
        off_peak: [...(sched.weekend?.off_peak || [])],
      },
    };
  }

  // Read per-tier prices from the operating-mode select's `tou_prices`
  // attribute (Phase 4.1++). Null entries indicate the cube has no current
  // price for that tier (empty / all-shim) so the input will fall back to
  // the placeholder. Returns null if the whole attribute is missing (older
  // integration version), so the card degrades gracefully.
  _readCubePrices() {
    const state = this.hass?.states?.["select.ep_cube_operating_mode"];
    const prices = state?.attributes?.tou_prices;
    if (!prices || typeof prices !== "object") return null;
    const pickProfile = (p) => ({
      peak:     typeof p?.peak     === "number" ? p.peak     : null,
      mid_peak: typeof p?.mid_peak === "number" ? p.mid_peak : null,
      off_peak: typeof p?.off_peak === "number" ? p.off_peak : null,
    });
    return {
      workday: pickProfile(prices.workday),
      weekend: pickProfile(prices.weekend),
    };
  }

  _hydrateFromCube() {
    const fromCube = this._readCubeSchedule();
    if (!fromCube) return;
    this._schedule = fromCube;
    const prices = this._readCubePrices();
    if (prices) this._prices = prices;
    this._hydrated = true;
    this._errors = [];
  }

  _onReloadFromCube() {
    const fromCube = this._readCubeSchedule();
    if (!fromCube) {
      this._statusMsg = "Cube schedule not available yet — try again in a moment.";
      return;
    }
    this._schedule = fromCube;
    const prices = this._readCubePrices();
    if (prices) this._prices = prices;
    this._errors = [];
    this._statusMsg = "Reloaded from cube.";
  }

  // Price input handler. Empty string → null (means "preserve cube's
  // current price on save"). Non-empty → parsed as Number; NaN stays as the
  // empty-string sentinel via the same null path. Bounds-check happens at
  // save time via validatePrices so the user can transit through invalid
  // states while typing.
  _onPriceChange(profile, tier, raw) {
    const trimmed = String(raw ?? "").trim();
    let next = null;
    if (trimmed !== "") {
      const n = Number(trimmed);
      if (Number.isFinite(n)) next = n;
    }
    this._prices = {
      ...this._prices,
      [profile]: { ...this._prices[profile], [tier]: next },
    };
    this._statusMsg = "";
  }

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

  // Pick a default for new slots that won't trip the overlap validator on
  // first edit. Use the latest end-time across all tiers on this profile,
  // so the new slot tails the existing schedule. Empty schedule starts at
  // 00:00. Duration defaults to 1h, capped at 23:59 (the cube's
  // midnight-end convention).
  _defaultNewSlot(profile) {
    let latestEnd = 0;
    for (const tier of TIERS) {
      for (const slot of this._schedule[profile][tier.key] || []) {
        const split = splitSlot(slot);
        if (!split) continue;
        const endM = minutesOfDay(split.end);
        if (endM > latestEnd) latestEnd = endM;
      }
    }
    // Day's already full (or nearly) — fall back to a 23:00-23:59 stub so
    // the new slot at least has a non-zero duration to edit.
    const startMin = latestEnd >= 1439 ? 1380 : latestEnd;
    const endMin = Math.min(startMin + 60, 1439);
    return `${minutesToHHMM(startMin)}-${minutesToHHMM(endMin)}`;
  }

  _addSlot(profile, tier) {
    const slots = [...(this._schedule[profile][tier] || []), this._defaultNewSlot(profile)];
    this._schedule = {
      ...this._schedule,
      [profile]: { ...this._schedule[profile], [tier]: slots },
    };
    this._statusMsg = "";
  }

  async _onClearSchedule() {
    const ok = window.confirm(
      "Clear ALL TOU schedule slots (workday + weekend)?\n\n" +
      "This wipes every tier on both profiles and saves the empty schedule " +
      "to the cube. Useful for starting fresh or removing leftover slots " +
      "from prior Predbat overrides. Can't be undone — but you can paint " +
      "new slots immediately after.\n\nPer-tier prices are left untouched.",
    );
    if (!ok) return;
    this._schedule = {
      workday: { peak: [], mid_peak: [], off_peak: [] },
      weekend: { peak: [], mid_peak: [], off_peak: [] },
    };
    // Don't clear `_prices` — the user may want to repaint slots right after
    // Clear all, and their last-typed prices are still meaningful. Hydration
    // after save will refresh from cube anyway.
    this._errors = [];
    // _onSave reads from this._schedule, so it'll push the cleared state.
    await this._onSave();
  }

  _copyFromOtherProfile() {
    const dest = this._activeProfile;
    const src = dest === "workday" ? "weekend" : "workday";
    const destHasContent =
      TIERS.some((t) => (this._schedule[dest][t.key] || []).length > 0) ||
      TIERS.some((t) => this._prices[dest][t.key] !== null);
    if (destHasContent) {
      const ok = window.confirm(
        `Replace ${dest} schedule + prices with a copy of ${src}? ` +
        `This can't be undone.`,
      );
      if (!ok) return;
    }
    const source = this._schedule[src];
    const sourcePrices = this._prices[src];
    this._schedule = {
      ...this._schedule,
      [dest]: {
        peak: [...source.peak],
        mid_peak: [...source.mid_peak],
        off_peak: [...source.off_peak],
      },
    };
    this._prices = {
      ...this._prices,
      [dest]: {
        peak: sourcePrices.peak,
        mid_peak: sourcePrices.mid_peak,
        off_peak: sourcePrices.off_peak,
      },
    };
    this._errors = [];
    this._statusMsg = `Copied ${src} → ${dest}.`;
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

  // Validate the rate inputs against PRICE_MIN/MAX. Empty (null) prices
  // are fine — they mean "preserve cube's current price". Mirrors the
  // `_PRICES_SCHEMA` range in services.py.
  _validatePrices() {
    const errs = [];
    for (const profile of ["workday", "weekend"]) {
      for (const tier of TIERS) {
        const v = this._prices[profile][tier.key];
        if (v === null) continue;
        if (!Number.isFinite(v) || v < PRICE_MIN || v > PRICE_MAX) {
          errs.push(
            `${profile === "workday" ? "Workday" : "Weekend"} ${tier.label}: ` +
            `rate must be ${PRICE_MIN}-${PRICE_MAX} p/kWh (got "${v}")`,
          );
        }
      }
    }
    return errs;
  }

  // Collect explicit prices into the wire-side `prices` dict. Skip null
  // entries (which signal "preserve from cube" — the backend's
  // _existing_house_price + DEFAULT_TIER_PRICE_* fallback handles them).
  _collectPrices() {
    const out = {};
    for (const profile of ["workday", "weekend"]) {
      for (const tier of TIERS) {
        const v = this._prices[profile][tier.key];
        if (v === null) continue;
        out[`${tier.key}_${profile}`] = v;
      }
    }
    return out;
  }

  async _onSave() {
    const errors = [
      ...validateDay(this._schedule.workday).map((e) => `Workday: ${e}`),
      ...validateDay(this._schedule.weekend).map((e) => `Weekend: ${e}`),
      ...this._validatePrices(),
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
      const prices = this._collectPrices();
      if (Object.keys(prices).length > 0) data.prices = prices;
      if (this._config?.device_id) data.device_id = this._config.device_id;
      await this.hass.callService("ep_cube", "set_tou_schedule", data);
      this._statusMsg = "Schedule saved.";
      // Re-hydrate so the card reflects the cube's now-current state. The
      // service triggers a coordinator refresh before returning, so the
      // select entity's tou_schedule + tou_prices attributes are fresh by
      // the time we read.
      this._hydrateFromCube();
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
    const priceValue = this._prices[profile][tier.key];
    // Display the user's current value (numeric) or empty string when null
    // (means "preserve cube"). Placeholder shows the tier's default so the
    // user has a visible hint of what they'd get if they leave it blank.
    const priceDisplay = priceValue === null ? "" : String(priceValue);
    return html`
      <div class="tier">
        <div class="tier-header" style="--tier-colour: ${tier.colour}">
          <span class="tier-dot"></span>
          <span class="tier-label">${tier.label}</span>
          <div class="tier-rate">
            <input
              type="number"
              step="0.01"
              min=${PRICE_MIN}
              max=${PRICE_MAX}
              .value=${priceDisplay}
              placeholder=${tier.defaultPrice.toFixed(2)}
              @input=${(e) => this._onPriceChange(profile, tier.key, e.target.value)}
              title="Rate in p/kWh. Leave blank to keep the cube's current price."
            />
            <span class="unit">p/kWh</span>
          </div>
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

          <div class="tab-actions">
            <button class="copy-link" @click=${this._onReloadFromCube}>
              Reload from cube
            </button>
            <button class="copy-link" @click=${this._copyFromOtherProfile}>
              Copy from ${profile === "workday" ? "weekend" : "workday"}
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
              <button
                class="clear"
                ?disabled=${this._saving}
                @click=${this._onClearSchedule}
                title="Wipe all slots on both profiles and save the empty schedule."
              >
                Clear all
              </button>
              <span class="status">${this._statusMsg}</span>
            </div>

            <div class="hint">
              <strong>Tip:</strong> you only need to paint off-peak slots
              (when you want to force grid charging at cheap rates). Leave
              peak/mid-peak empty — undefined time defaults to peak, which
              the cube treats as self-consumption (battery powers loads, no
              grid charge). DST tier lists and reserves are preserved from
              the cube's current state. Empty tier = cleared on save. Slots
              can't cross midnight — use 23:59 to end a slot at the end of
              the day (matches the EP Cube mobile app's convention).
              <br /><br />
              <strong>Rates:</strong> the p/kWh inputs let you set each
              tier's price. Leave blank to keep the cube's current value.
              For Predbat users the cube's internal prices are cosmetic —
              Predbat optimises against your real tariff (e.g. Octopus
              Agile) independently.
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
      .tab-actions {
        display: flex;
        justify-content: flex-end;
        gap: 16px;
        margin: -8px 0 12px 0;
      }
      .copy-link {
        background: transparent;
        border: none;
        color: var(--primary-color);
        font-size: 0.85em;
        cursor: pointer;
        padding: 4px 0;
        text-decoration: underline;
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
      .tier-rate {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: 0.85em;
      }
      .tier-rate input {
        width: 72px;
        padding: 4px 6px;
        background: var(--card-background-color, #1c1c1c);
        color: var(--primary-text-color);
        border: 1px solid var(--divider-color, #444);
        border-radius: 6px;
        font: inherit;
        text-align: right;
      }
      .tier-rate .unit {
        opacity: 0.7;
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
      .clear {
        background: transparent;
        color: var(--error-color, #db4437);
        border: 1px solid var(--error-color, #db4437);
        border-radius: 8px;
        padding: 10px 16px;
        cursor: pointer;
        font-size: 0.95em;
      }
      .clear:disabled {
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
