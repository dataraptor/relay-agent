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
