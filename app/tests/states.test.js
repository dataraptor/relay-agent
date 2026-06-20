/**
 * Split 09 — T1: per-case mapper tests for every §20/§9 edge state (R1 table).
 *
 * Each test feeds a real RunView fixture (a Split-07 projection from `_gen_fixtures.py`) or a
 * hand-written canned RunView/error for a state the engine cannot yet produce, and asserts the
 * mapper yields the right honest treatment. Every state is read from a **real RunView/error field**
 * — never a simulated client flag (E6). Markers the RunView does not yet carry
 * (`refusal`/`step_capped`/step `is_error`) are consumed *if present* and otherwise absent
 * (Split-09 carry-forward); they are exercised here with canned fixtures, which T1 sanctions.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const RelayMap = require("../map.js");

const FX = path.join(__dirname, "fixtures");
const fixture = (name) => JSON.parse(fs.readFileSync(path.join(FX, name), "utf8"));

// --- Low confidence (§9 confidence=low) ------------------------------------

test("low confidence → 'biases to route/escalate' triage hint", () => {
  const vm = RelayMap.runViewToState(fixture("ambiguous_escalate.json"));
  assert.equal(vm.triage.confidence, "low");
  assert.ok(vm.triageHints.includes("low → biases to route / escalate"));
});

// --- Ambiguous (route/escalate, no write) ----------------------------------

test("ambiguous → escalate auto, 0 writes pending, ambiguous notice + auto row", () => {
  const vm = RelayMap.runViewToState(fixture("ambiguous_escalate.json"));
  assert.equal(vm.status, "done");
  assert.equal(vm.pendingCount, 0);
  const auto = vm.trace.find((r) => r.kind === "auto");
  assert.ok(auto && auto.tool === "escalate", "escalate shows as an auto row");
  const notice = vm.notices.find((n) => n.kind === "ambiguous");
  assert.ok(notice, "an ambiguous notice is surfaced");
  assert.match(notice.text, /0 writes/);
});

// --- Prompt-injection (the dark-beat) --------------------------------------

test("injection → gate still rises; injection caption driven by the locked-example signal", () => {
  const rv = fixture("send_reply_pending.json");
  // No injection hint → no caption (a normal gated write).
  const plain = RelayMap.runViewToState(rv);
  assert.equal(plain.pending.injection, false);
  // The locked /examples flag (a real backend signal) → the dark-beat caption, gate still pauses.
  const flagged = RelayMap.runViewToState(rv, { injectionHint: true });
  assert.equal(flagged.status, "awaiting_approval");
  assert.ok(flagged.pending, "the gate still rises on the injection ticket");
  assert.equal(flagged.pending.injection, true);
});

// --- all_grounded=false (faithfulness fail) --------------------------------

test("all_grounded=false → outline alert + per-claim list + 'model may revise'", () => {
  const rv = {
    status: "done",
    triage: { intent: "general_question", priority: "normal", confidence: "medium", extracted_fields: {} },
    trace: [
      {
        seq: 0,
        tool: "draft_reply",
        cls: "read_class",
        args: {},
        state: "executed",
        draft: {
          body: "Refunds are instant.",
          citations: [],
          faithfulness: {
            all_grounded: false,
            claims: [
              { claim: "Refunds are instant", label: "CONTRADICTED" },
              { claim: "We refund duplicates", label: "SUPPORTED" },
            ],
          },
        },
      },
    ],
    cost: {},
  };
  const reply = RelayMap.runViewToState(rv).trace[0];
  assert.equal(reply.grounded, false);
  assert.equal(reply.glyph, "alert");
  assert.equal(reply.faithLabel, "Not grounded");
  assert.equal(reply.mayRevise, true);
  assert.deepEqual(
    reply.claims.map((c) => c.label),
    ["CONTRADICTED", "SUPPORTED"]
  );
});

// --- Claude refusal (consumed-if-present; carry-forward) -------------------

test("refusal marker → neutral 'declined/refused' notice with the raw reason", () => {
  const vm = RelayMap.runViewToState({
    status: "done",
    triage: { intent: "general_question", priority: "normal", confidence: "medium", extracted_fields: {} },
    trace: [],
    cost: { total_usd: 0.0007, by_call: [{ kind: "triage", cost_usd: 0.0007 }] },
    refusal: { reason: "I can't help with that." },
  });
  const n = vm.notices.find((x) => x.kind === "refusal");
  assert.ok(n, "a refusal notice is surfaced");
  assert.match(n.text, /declined|refused/i);
  assert.equal(n.detail, "I can't help with that.");
  assert.ok(vm.cost > 0, "partial cost is still shown");
});

// --- Loop step-cap (consumed-if-present; carry-forward) --------------------

test("step_capped marker → terminal 'reached step cap (6)' notice; cost preserved", () => {
  const vm = RelayMap.runViewToState({
    status: "done",
    triage: { intent: "technical_issue", priority: "high", confidence: "high", extracted_fields: {} },
    trace: [{ seq: 0, tool: "lookup_customer", cls: "read", args: {}, state: "executed" }],
    cost: { total_usd: 0.01, by_call: [{ kind: "triage", cost_usd: 0.01 }] },
    step_capped: true,
  });
  const n = vm.notices.find((x) => x.kind === "step_cap");
  assert.ok(n, "a step-cap notice is surfaced");
  assert.match(n.text, /step cap \(6\)/);
  assert.ok(vm.cost > 0);
});

// --- Backend conflict / is_error (consumed-if-present; carry-forward) ------

test("trace step is_error → alert row + error sub-line (model adapts)", () => {
  const vm = RelayMap.runViewToState({
    status: "done",
    trace: [
      {
        seq: 0,
        tool: "update_ticket",
        cls: "read",
        args: { ticket_id: "T-9" },
        state: "executed",
        is_error: true,
        error: "ticket not found",
      },
    ],
    cost: {},
  });
  const row = vm.trace[0];
  assert.equal(row.isError, true);
  assert.equal(row.glyph, "alert");
  assert.equal(row.errorLine, "ticket not found");
});

// --- deny policy → blocked --------------------------------------------------

test("blocked write → blocked row, no gate buttons (records untouched)", () => {
  const vm = RelayMap.runViewToState({
    status: "done",
    triage: { intent: "spam", priority: "low", confidence: "high", extracted_fields: {} },
    trace: [
      { seq: 0, tool: "send_reply", cls: "state_change", args: { to: "x@y.com" }, state: "blocked", decision: "blocked" },
    ],
    cost: {},
  });
  assert.equal(vm.trace[0].kind, "blocked");
  assert.equal(vm.trace[0].result, "blocked by policy");
  assert.equal(vm.pending, null); // no gate buttons
});

// --- auto policy write ------------------------------------------------------

test("auto write → outlined 'auto' row, distinct from approved", () => {
  const vm = RelayMap.runViewToState(fixture("tech_auto.json"));
  const auto = vm.trace.find((r) => r.kind === "auto");
  assert.ok(auto);
  assert.equal(auto.glyph, "auto");
  assert.notEqual(auto.kind, "approved");
});

// --- Below cache floor (benign, not an error) ------------------------------

test("Anthropic cache_read=0 → 'below cache floor' caption, not an error", () => {
  const vm = RelayMap.runViewToState(fixture("billing_awaiting.json"));
  assert.equal(vm.tokens.cache_read, 0);
  assert.equal(vm.belowCacheFloor, true);
  assert.equal(vm.cacheCaption, "below cache floor — no benefit (expected)");
});

test("OpenAI → honest 'no prompt cache' caption (not credited)", () => {
  assert.equal(RelayMap.cacheCaption("openai", { cache_read: 0 }), "no prompt cache on the OpenAI path (expected)");
  assert.equal(RelayMap.cacheCaption("anthropic", { cache_read: 3900 }), "cache_read 3,900 tokens reused");
});

// --- Spam -------------------------------------------------------------------

test("spam → 'no action warranted' triage hint; 0 writes", () => {
  const vm = RelayMap.runViewToState(fixture("spam_noaction.json"));
  assert.equal(vm.triage.intent, "spam");
  assert.ok(vm.triageHints.includes("intent: spam — no action warranted"));
  assert.equal(vm.pendingCount, 0);
});

// --- Error anywhere (status=error) -----------------------------------------

test("status=error → message surfaced, partial cost still shown (§5.7)", () => {
  const vm = RelayMap.runViewToState({
    status: "error",
    triage: { intent: "billing_dispute", priority: "high", confidence: "medium", extracted_fields: {} },
    trace: [{ seq: 0, tool: "lookup_customer", cls: "read", args: {}, state: "executed" }],
    cost: { total_usd: 0.002, by_call: [{ kind: "triage", cost_usd: 0.002 }] },
    error: "provider stream interrupted",
  });
  const n = vm.notices.find((x) => x.kind === "error");
  assert.ok(n, "an error notice is surfaced");
  assert.equal(n.detail, "provider stream interrupted");
  assert.ok(vm.cost > 0, "partial cost survives the error");
});

// --- totality: every new field is present + safe on empty input ------------

test("edge fields are total on an empty RunView", () => {
  const vm = RelayMap.runViewToState({});
  assert.deepEqual(vm.pendings, []);
  assert.equal(vm.multiPending, false);
  assert.equal(vm.pendingCount, 0);
  assert.deepEqual(vm.triageHints, []);
  assert.deepEqual(vm.notices, []);
  assert.equal(typeof vm.cacheCaption, "string");
});
