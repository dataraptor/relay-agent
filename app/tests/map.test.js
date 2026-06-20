/**
 * Split 08 — T1: mapper purity + fidelity.
 *
 * Drives `runViewToState()` against the canned RunView fixtures (real Split 07 projections, see
 * `_gen_fixtures.py`) and asserts every view-model field the template binds to is produced
 * correctly: null fields stay null, citations + per-claim faithfulness map through, the records
 * state machine transitions, `costBreakdown` sums to the total, and the mapper is pure (no input
 * mutation, deterministic, total on partial input).
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const RelayMap = require("../map.js");

const FX = path.join(__dirname, "fixtures");
function fixture(name) {
  return JSON.parse(fs.readFileSync(path.join(FX, name), "utf8"));
}

const billingAwaiting = fixture("billing_awaiting.json");
const billingApproved = fixture("billing_approved.json");
const billingRejected = fixture("billing_rejected.json");
const techAuto = fixture("tech_auto.json");
const config = fixture("config.json");

// --- billing, awaiting approval (the money moment) --------------------------

test("billing awaiting: triage maps, null fields stay null", () => {
  const vm = RelayMap.runViewToState(billingAwaiting);
  assert.equal(vm.status, "awaiting_approval");
  assert.equal(vm.triage.intent, "billing_dispute");
  assert.equal(vm.triage.priority, "high");
  assert.equal(vm.triage.confidence, "medium");
  assert.equal(vm.triage.fields.customer_email, "jane@acme.com");
  assert.equal(vm.triage.fields.order_ref, "A-4471");
  assert.equal(vm.triage.fields.product, "Pro");
  assert.equal(vm.triage.fields.amount, null); // null stays null (rendered as the muted token)
});

test("billing awaiting: trace rows have the right kinds + content", () => {
  const vm = RelayMap.runViewToState(billingAwaiting);
  const kinds = vm.trace.map((r) => r.kind);
  assert.deepEqual(kinds, ["read", "read", "reply", "awaiting"]);

  const [lookup, search, reply, awaiting] = vm.trace;
  assert.equal(lookup.tool, "lookup_customer");
  assert.equal(lookup.argsLine, 'email "jane@acme.com"');
  assert.equal(lookup.result, "Pro · active · flags: double_charge_detected");
  assert.equal(lookup.latency, "7ms");

  assert.equal(search.argsLine, '"duplicate charge refund policy"'); // search_kb shows the query

  assert.equal(reply.kind, "reply");
  assert.match(reply.replyBody, /duplicate charge/);
  assert.equal(reply.streaming, false);
  assert.equal(reply.citations.length, 1);
  assert.equal(reply.citations[0].chunk_id, "kb-refund-001");
  assert.equal(reply.citations[0].label, "Billing Policy · #kb-refund-001");
  assert.match(reply.citations[0].text, /Duplicate charges are refunded/);
  assert.match(reply.citations[0].url, /refunds#duplicate/);
  assert.equal(reply.grounded, true);
  assert.equal(reply.faithLabel, "Grounded (0/0)");

  assert.equal(awaiting.kind, "awaiting");
  assert.equal(awaiting.tool, "update_ticket");
  assert.equal(awaiting.glyph, "arrow");
});

test("billing awaiting: pending maps with arg editability + rationale", () => {
  const vm = RelayMap.runViewToState(billingAwaiting);
  assert.equal(vm.pendingRowIndex, 3);
  const p = vm.pending;
  assert.equal(p.id, "tc_update_ticket");
  assert.equal(p.tool, "update_ticket");
  assert.equal(p.rationale, "Proposing a status update for your approval.");
  assert.equal(p.proposedStatus, "pending_refund");
  assert.equal(p.irreversible, false); // not send_reply
  // ticket_id first (the gate diff reads args[0]) and read-only; status/note editable.
  assert.equal(p.args[0].key, "ticket_id");
  assert.equal(p.args[0].value, "T-1042");
  assert.equal(p.args[0].editable, false);
  const byKey = Object.fromEntries(p.args.map((a) => [a.key, a]));
  assert.equal(byKey.status.editable, true);
  assert.equal(byKey.note.editable, true);
});

test("billing awaiting: records populate + proposed diff", () => {
  const vm = RelayMap.runViewToState(billingAwaiting);
  assert.equal(vm.recordsState, "proposed");
  assert.equal(vm.proposedStatus, "pending_refund");
  const r = vm.records;
  assert.equal(r.email, "jane@acme.com");
  assert.equal(r.plan, "Pro");
  assert.equal(r.status, "active");
  assert.equal(r.flag, "double_charge_detected");
  assert.equal(r.ticketId, "#T-1042");
  assert.equal(r.ticketStatus, "open"); // not yet committed — the write is still pending
  assert.equal(r.queue, "unassigned");
});

test("billing awaiting: cost breakdown sums to the total", () => {
  const vm = RelayMap.runViewToState(billingAwaiting);
  const sum = vm.costBreakdown.reduce((acc, c) => acc + c.cost, 0);
  assert.ok(Math.abs(sum - vm.cost) < 1e-9, `breakdown ${sum} != total ${vm.cost}`);
  assert.ok(vm.cost > 0);
  assert.deepEqual(
    vm.costBreakdown.map((c) => c.kind),
    ["triage", "loop_step", "loop_step", "loop_step", "faithfulness", "loop_step"]
  );
  assert.equal(vm.tokens.in, 640);
  assert.equal(vm.tokens.out, 128);
  assert.equal(vm.tokens.cache_read, 0);
  assert.match(vm.latency, /^\d+\.\ds$/);
});

test("billing awaiting: no outcome yet (still paused)", () => {
  const vm = RelayMap.runViewToState(billingAwaiting);
  assert.equal(vm.outcome, null);
});

// --- billing, approved (write committed) ------------------------------------

test("billing approved: commit + approved row + outcome", () => {
  const vm = RelayMap.runViewToState(billingApproved);
  assert.equal(vm.status, "done");
  assert.equal(vm.recordsState, "committed");
  assert.equal(vm.records.ticketStatus, "pending_refund"); // the committed write
  assert.equal(vm.pending, null);

  const last = vm.trace[vm.trace.length - 1];
  assert.equal(last.kind, "approved");
  assert.equal(last.tool, "update_ticket");
  assert.equal(last.result, "approved · by you");

  assert.equal(vm.outcome.statusLine, "done · 1 reply (grounded) · 1 approved");
  assert.match(vm.outcome.summary, /Looked up the customer/);
  assert.match(vm.outcome.summary, /marked the ticket pending_refund \(approved\)/);
});

// --- billing, rejected (no change) ------------------------------------------

test("billing rejected: nochange + rejected row + outcome", () => {
  const vm = RelayMap.runViewToState(billingRejected);
  assert.equal(vm.status, "done");
  assert.equal(vm.recordsState, "nochange");
  assert.equal(vm.records.ticketStatus, "open"); // never changed
  const last = vm.trace[vm.trace.length - 1];
  assert.equal(last.kind, "rejected");
  assert.equal(last.result, "rejected — no change");
  assert.equal(vm.outcome.statusLine, "done · 1 reply (grounded) · 1 rejected");
});

// --- tech, auto-route -------------------------------------------------------

test("tech auto: auto row + committed + outcome", () => {
  const vm = RelayMap.runViewToState(techAuto);
  assert.equal(vm.status, "done");
  assert.equal(vm.recordsState, "committed");
  assert.equal(vm.records.queue, "tech");
  const auto = vm.trace.find((r) => r.kind === "auto");
  assert.ok(auto, "an auto row exists");
  assert.equal(auto.tool, "route_ticket");
  assert.equal(auto.result, "route_ticket auto-approved by policy");
  assert.equal(auto.argsLine, 'ticket_id "T-1050" · queue "tech"');
  assert.equal(vm.outcome.statusLine, "done · 1 auto");
  assert.match(vm.outcome.summary, /routed the ticket to tech \(auto\)/);
});

// --- purity + totality ------------------------------------------------------

test("mapper is pure: deterministic + does not mutate input", () => {
  const input = fixture("billing_awaiting.json");
  const before = JSON.stringify(input);
  const a = RelayMap.runViewToState(input);
  const b = RelayMap.runViewToState(input);
  assert.deepEqual(a, b); // deterministic
  assert.equal(JSON.stringify(input), before); // input untouched
});

test("mapper is total: empty/partial RunView never throws", () => {
  assert.doesNotThrow(() => RelayMap.runViewToState({}));
  assert.doesNotThrow(() => RelayMap.runViewToState(undefined));
  assert.doesNotThrow(() => RelayMap.runViewToState({ status: "error", triage: null }));
  const vm = RelayMap.runViewToState({});
  assert.equal(vm.triage, null);
  assert.deepEqual(vm.trace, []);
  assert.equal(vm.pending, null);
  assert.equal(vm.records, null);
  assert.equal(vm.recordsState, "empty");
  assert.equal(vm.cost, 0);
  assert.equal(vm.outcome, null);
});

test("every region the template binds to has a mapped source (E4)", () => {
  const vm = RelayMap.runViewToState(billingAwaiting);
  for (const key of [
    "triage",
    "trace",
    "pending",
    "records",
    "recordsState",
    "proposedStatus",
    "cost",
    "costBreakdown",
    "tokens",
    "latency",
    "run_id",
    "status",
  ]) {
    assert.ok(key in vm, `view-model is missing ${key}`);
  }
});

// --- T3: config-driven options (not hardcoded) ------------------------------

test("config view: providers/models/policies come from /config", () => {
  assert.deepEqual(RelayMap.providersFrom(config), ["anthropic", "openai"]);
  assert.deepEqual(RelayMap.policiesFrom(config), ["auto", "default", "strict"]);
  // switching provider swaps the model list from models_by_provider
  assert.deepEqual(RelayMap.modelsFor(config, "anthropic"), [
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-8",
  ]);
  assert.deepEqual(RelayMap.modelsFor(config, "openai"), ["gpt-5.5"]);
  assert.equal(RelayMap.defaultModelFor(config, "anthropic"), "claude-sonnet-4-6");
  assert.equal(RelayMap.defaultModelFor(config, "openai"), "gpt-5.5");
});

test("config view: graceful fallback with no config", () => {
  assert.deepEqual(RelayMap.providersFrom(null), ["anthropic", "openai"]);
  assert.deepEqual(RelayMap.modelsFor(null, "anthropic"), []);
  assert.deepEqual(RelayMap.policiesFrom(undefined), ["auto", "default", "strict"]);
  assert.equal(RelayMap.defaultModelFor(null, "openai"), "");
});
