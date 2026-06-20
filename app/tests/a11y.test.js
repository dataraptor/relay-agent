/**
 * Split 09 — T6/T7 (CI-runnable portion): static a11y + responsive + multi-pending wiring.
 *
 * A Node test runner has no DOM/axe, so the automated keyboard + axe + visual-responsive passes are
 * a documented manual checklist (see PROGRESS.md). What CI *can* guarantee is that the a11y hooks
 * the design requires are actually present in the served markup/CSS, and that the batch gate is
 * wired — so a regression that drops `aria-live`, the focus ring, the reduced-motion handling, or
 * the multi-pending Resume path fails the build.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const HTML = fs.readFileSync(path.join(__dirname, "..", "Relay.dc.html"), "utf8");

// --- screen-reader announcements (§9) --------------------------------------

test("the gate has an assertive aria-live region; streaming reply is polite", () => {
  assert.match(HTML, /aria-live="assertive"/);
  assert.match(HTML, /aria-live="polite"/);
  assert.ok(HTML.includes("{{ liveMessage }}"), "the assertive region binds the announcement");
});

test("the gate is a labelled modal dialog", () => {
  assert.match(HTML, /role="dialog"/);
  assert.match(HTML, /aria-modal="true"/);
  assert.match(HTML, /aria-label="Approval required"/);
});

// --- keyboard + focus (§9) -------------------------------------------------

test("the gate handles keyboard (onKeyDown) and has a focusable primary target", () => {
  assert.ok(HTML.includes('onKeyDown="{{ onGateKey }}"'), "gate keydown handler wired");
  assert.ok(HTML.includes('id="rl-gate-primary"'), "primary button is targetable for focus-on-open");
  assert.ok(HTML.includes('tabIndex="-1"'), "the sheet is programmatically focusable/trappable");
});

test("a visible 2px indigo focus ring is defined for interactive elements", () => {
  assert.match(HTML, /:focus-visible\{[^}]*outline:2px solid #4F46E5/);
});

// --- reduced motion (§9) ---------------------------------------------------

test("prefers-reduced-motion collapses animation; component skips token streaming", () => {
  assert.match(HTML, /@media \(prefers-reduced-motion: reduce\)/);
  assert.ok(HTML.includes("this._reduced"), "the component reads the reduced-motion preference");
  // the reply is pushed complete (no streamReply) when reduced
  assert.ok(/reduced\s*\?\s*0/.test(HTML), "the cadence collapses to instant under reduced motion");
});

// --- touch targets (§8/§9 ≥44px) -------------------------------------------

test("primary/interactive targets are ≥44px (run, approve, edit/reject, config, batch)", () => {
  // Run / Approve / Resume = 52px; edit/reject = 46px; config segments + batch buttons = 44px.
  assert.match(HTML, /height:52px/); // run + approve + resume
  assert.match(HTML, /height:46px/); // edit / reject
  assert.ok(HTML.includes("height:44px"), "config segments / batch buttons are ≥44px");
  assert.ok(!/height:40px/.test(HTML), "no sub-44px interactive control remains");
});

// --- responsive (§8) — fluid + bottom-sheet-on-phone retained --------------

test("layout is fluid (clamp gutters) with a phone bottom-sheet treatment", () => {
  assert.match(HTML, /clamp\(16px,4vw,24px\)/); // fluid gutters scale 390px → desktop
  assert.match(HTML, /@media \(max-width:640px\)/); // gate becomes a bottom sheet on phones
  assert.ok(HTML.includes("max-width:820px"), "the run column centers + holds at every width");
});

// --- multi-pending batch wiring (R2) ---------------------------------------

test("the batch gate + Resume path is wired (turn-granular §8)", () => {
  assert.ok(HTML.includes("{{ gateBatch }}"), "the batch sheet is rendered");
  assert.ok(HTML.includes("{{ gateSingle }}"), "the single-pending fast path is kept");
  assert.ok(HTML.includes("RelayMap.allDecided"), "Resume is gated on all-decided");
  assert.ok(HTML.includes("RelayMap.buildDecisions"), "a single batch /approve covers every action");
  assert.ok(HTML.includes("{{ onResume }}"), "the Resume CTA is wired");
});
