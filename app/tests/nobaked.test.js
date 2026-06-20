/**
 * Split 08 — T4: no baked data + the API modules are wired.
 *
 * Static inspection of Relay.dc.html: the simulation (`buildScenario`, `texts()`, the canned
 * scenario data, the hardcoded provider/model lists) must be gone, and the component must drive
 * itself from the real API (`api.handle` / `api.approve`) and the mapper (`RelayMap`). The render
 * layer (the `x-dc` template + `renderVals`) must still be present — this split swaps the data
 * source, it does not rebuild the UI.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const HTML = fs.readFileSync(path.join(__dirname, "..", "Relay.dc.html"), "utf8");

test("the simulation is gone", () => {
  for (const token of [
    "buildScenario",
    "texts()",
    "this._sc", // the old scenario handle
    "gpt-mid", // hardcoded model list
    "gpt-large",
    "sonnet-4-6", // hardcoded model id (now comes from /config)
    "saved ~$0.004", // baked cache caption
  ]) {
    assert.ok(!HTML.includes(token), `simulation leftover found: ${token}`);
  }
});

test("the component is wired to the real API + mapper", () => {
  assert.ok(HTML.includes('src="./api.js"'), "api.js script tag");
  assert.ok(HTML.includes('src="./map.js"'), "map.js script tag");
  assert.ok(HTML.includes("window.RelayApi.makeClient"), "constructs the API client");
  assert.ok(HTML.includes("this.api.handle("), "run() calls /handle");
  assert.ok(HTML.includes("this.api.approve("), "approve() calls /approve");
  assert.ok(HTML.includes("window.RelayMap.runViewToState"), "maps the RunView");
  assert.ok(HTML.includes("this.api.getConfig()"), "loads /config");
  assert.ok(HTML.includes("this.api.getExamples()"), "loads /examples");
});

test("the render layer is preserved (UI not rebuilt)", () => {
  assert.ok(HTML.includes("<x-dc>"), "the design template is intact");
  assert.ok(HTML.includes("renderVals()"), "the render logic is intact");
  assert.ok(HTML.includes("class Component extends DCLogic"), "the DC component is intact");
  // config-driven options replaced the hardcoded arrays
  assert.ok(HTML.includes("window.RelayMap.modelsFor(cfg"), "models come from /config");
  assert.ok(HTML.includes("keyBanner: st.keyBanner"), "the missing-key banner is wired");
});

// --- Split 09 (E6): no simulated edge signals; every state from a real field ---

test("the injection caption is driven by the real /examples lock flag, not a baked flag", () => {
  // The mapper no longer hardcodes injection:false; the component derives it from the locked example.
  assert.ok(HTML.includes("isInjectionExample"), "injection derived from the example's lock flag");
  assert.ok(HTML.includes("injectionHint"), "the hint is passed into the mapper");
  assert.ok(!/injection\s*:\s*true/.test(HTML), "no hardcoded injection:true flag");
});

test("edge states + multi-pending are read from the mapped RunView, not invented", () => {
  // notices / triage hints / pendings all flow from the mapper's view-model.
  assert.ok(HTML.includes("notices:vm.notices") || HTML.includes("notices: noticeList"));
  assert.ok(HTML.includes("vm.pendings"), "all pending actions come from the RunView");
  assert.ok(HTML.includes("vm.triageHints"), "triage hints come from the RunView triage");
  // the old single-pending-only assumption is gone
  assert.ok(!HTML.includes("buildScenario"), "no simulation");
});
