/**
 * Split 09 — component render check (E1/E7): `renderVals()` never crashes on any edge state.
 *
 * A Node runner has no browser, but the component's render layer is plain logic: extract the
 * `Component` class, give it a minimal React + the real mapper/client globals, drive its state from
 * each fixture/edge RunView exactly as the app does, and assert `renderVals()` returns a binding
 * object without throwing — and that the key Split-09 bindings (batch gate, notices, send_reply
 * reply, injection caption) are produced. This catches the "undefined.foo" render crash class that
 * would white-screen the app, for every §20/§9 state, in CI.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const RelayMap = require("../map.js");
const RelayApi = require("../api.js");

// --- a minimal host so the extracted class can run headless --------------------
function FakeEl(type, props) {
  return { __el: true, type: type, props: props || {} };
}
const FakeReact = { createElement: FakeEl, Fragment: "Fragment", isValidElement: (v) => !!(v && v.__el) };
class DCLogic {
  constructor(props) {
    this.props = props || {};
    this.state = {};
  }
  setState() {}
}
globalThis.window = { RelayMap: RelayMap, RelayApi: RelayApi };
globalThis.React = FakeReact;
globalThis.document = { getElementById: () => null, activeElement: null };

function loadComponent() {
  const html = fs.readFileSync(path.join(__dirname, "..", "Relay.dc.html"), "utf8");
  const src = html.match(/data-dc-script[^>]*>([\s\S]*?)<\/script>/)[1];
  // eslint-disable-next-line no-new-func
  const make = new Function("DCLogic", "React", src + "\n;return Component;");
  return make(DCLogic, FakeReact);
}
const Component = loadComponent();

const fixture = (name) =>
  JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", name), "utf8"));
const config = fixture("config.json");
const examples = fixture("examples.json");

/** Build a plausible component state for a mapped view-model (mirrors finishReveal/applyDecision). */
function stateForVm(vm, opts) {
  opts = opts || {};
  const awaiting = vm.status === "awaiting_approval" && vm.pendings.length > 0;
  return {
    phase: awaiting ? "awaiting_approval" : "done",
    example: opts.example || "billing",
    ticketText: "x",
    ticketCollapsed: true,
    examples: examples,
    config: config,
    provider: vm.provider || "anthropic",
    model: vm.model || "claude-sonnet-4-6",
    policy: "strict",
    configOpen: false,
    demoOpen: false,
    triageLoading: false,
    triage: vm.triage,
    triageHints: vm.triageHints,
    trace: vm.trace,
    pendingRowIndex: vm.pendingRowIndex,
    pending: vm.pending,
    pendings: vm.pendings,
    multiPending: vm.multiPending,
    choices: opts.choices || {},
    editing: false,
    edited: {},
    records: vm.records,
    recordsState: vm.recordsState,
    proposedStatus: vm.proposedStatus,
    cost: vm.cost,
    costBreakdown: vm.costBreakdown,
    tokens: vm.tokens,
    latency: vm.latency,
    cacheCaption: vm.cacheCaption,
    gateOpen: awaiting,
    stepLabel: "",
    outcome: vm.outcome,
    decision: null,
    notices: vm.notices,
    citeOpenIdx: null,
    faithOpen: false,
    costOpen: true,
    liveMessage: awaiting ? "Approval required" : "",
    keyBanner: "",
    run_id: vm.run_id,
  };
}

function render(rv, opts) {
  opts = opts || {};
  const c = new Component({ startExample: opts.example || "billing", policyDefault: "strict" });
  c.state = stateForVm(RelayMap.runViewToState(rv, opts.mapOpts), opts);
  return c.renderVals();
}

// --- every shipped + canned state renders without throwing ---------------------

const REAL = [
  "billing_awaiting.json",
  "billing_approved.json",
  "billing_rejected.json",
  "tech_auto.json",
  "multi_pending.json",
  "send_reply_pending.json",
  "ambiguous_escalate.json",
  "spam_noaction.json",
];

for (const name of REAL) {
  test(`renderVals does not throw for ${name}`, () => {
    const vals = render(fixture(name));
    assert.equal(typeof vals, "object");
    assert.ok("gateSingle" in vals && "gateBatch" in vals);
    assert.ok(Array.isArray(vals.trace));
  });
}

test("canned edge RunViews render without throwing (error / refusal / step-cap / is_error)", () => {
  const cannedList = [
    { status: "error", triage: { intent: "billing_dispute", priority: "high", confidence: "medium", extracted_fields: {} }, trace: [], cost: { total_usd: 0.001, by_call: [{ kind: "triage", cost_usd: 0.001 }] }, error: "boom" },
    { status: "done", triage: { intent: "general_question", priority: "normal", confidence: "low", extracted_fields: {} }, trace: [], cost: {}, refusal: { reason: "no" } },
    { status: "done", triage: { intent: "technical_issue", priority: "high", confidence: "high", extracted_fields: {} }, trace: [{ seq: 0, tool: "lookup_customer", cls: "read", args: {}, state: "executed", is_error: true, error: "not found" }], cost: {}, step_capped: true },
    {},
    undefined,
  ];
  for (const rv of cannedList) {
    assert.doesNotThrow(() => render(rv || {}));
  }
});

// --- the Split-09 bindings are actually produced -------------------------------

test("multi-pending → batch gate bindings; Resume gates on all-decided", () => {
  const rv = fixture("multi_pending.json");
  const undecided = render(rv);
  assert.equal(undecided.gateBatch, true);
  assert.equal(undecided.gateSingle, false);
  assert.equal(undecided.g_pendingCount, 2);
  assert.equal(undecided.g_pendings.length, 2);
  assert.equal(undecided.g_canResume, false); // nothing decided yet

  const ids = rv.actions_pending.map((p) => p.id);
  const decided = render(rv, { choices: { [ids[0]]: "allow", [ids[1]]: "reject" } });
  assert.equal(decided.g_canResume, true);
});

test("send_reply pending → single gate shows irreversible + the exact reply body", () => {
  const vals = render(fixture("send_reply_pending.json"));
  assert.equal(vals.gateSingle, true);
  assert.equal(vals.g_irreversible, true);
  assert.equal(vals.g_hasReply, true);
  assert.match(vals.g_replyBody, /duplicate charge/);
});

test("injection example → the dark-beat caption is on (driven by the locked example)", () => {
  const vals = render(fixture("send_reply_pending.json"), { example: "injection", mapOpts: { injectionHint: true } });
  assert.equal(vals.g_injection, true);
});

test("edge notices + triage hints surface in the bindings", () => {
  const spam = render(fixture("spam_noaction.json"));
  assert.ok(spam.t_hints.some((h) => /spam/.test(h.text)));
  const amb = render(fixture("ambiguous_escalate.json"));
  assert.ok(amb.showNotices);
  assert.ok(amb.notices.some((n) => /0 writes/.test(n.text)));
});
