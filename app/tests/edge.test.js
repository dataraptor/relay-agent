/**
 * Split 08 — edge-case coverage for the mapper + client: blocked writes, send_reply
 * (irreversible), unrecognized steps, records that carry only a customer or only a ticket, and the
 * no-fetch guard. These exercise the graceful-degradation paths the happy-path fixtures don't hit.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const RelayMap = require("../map.js");

test("blocked write → blocked row + blocked records state + status line", () => {
  const rv = {
    status: "done",
    triage: { intent: "spam", priority: "low", confidence: "high", extracted_fields: {} },
    trace: [
      {
        seq: 0,
        tool: "send_reply",
        cls: "state_change",
        args: { to: "x@y.com", body: "hi" },
        state: "blocked",
        decision: "blocked",
      },
    ],
    actions_pending: [],
    actions_taken: [],
    records: null,
    cost: { total_usd: 0.001, by_call: [{ kind: "triage", cost_usd: 0.001 }] },
  };
  const vm = RelayMap.runViewToState(rv);
  const row = vm.trace[0];
  assert.equal(row.kind, "blocked");
  assert.equal(row.result, "blocked by policy");
  assert.equal(vm.recordsState, "empty"); // no records touched
  assert.equal(vm.outcome.statusLine, "done · 1 blocked");
});

test("send_reply pending is flagged irreversible", () => {
  const rv = {
    status: "awaiting_approval",
    triage: { intent: "billing_dispute", priority: "high", confidence: "medium", extracted_fields: {} },
    trace: [{ seq: 0, tool: "send_reply", cls: "state_change", args: {}, state: "awaiting" }],
    actions_pending: [
      { id: "a1", tool: "send_reply", args: { to: "jane@acme.com", body: "hi" }, rationale: "reply" },
    ],
    actions_taken: [],
    cost: {},
  };
  const vm = RelayMap.runViewToState(rv);
  assert.equal(vm.pending.irreversible, true);
  assert.equal(vm.pending.proposedStatus, null);
});

test("ungrounded reply → 'Not grounded' + ungrounded outcome", () => {
  const rv = {
    status: "done",
    triage: { intent: "general_question", priority: "normal", confidence: "low", extracted_fields: {} },
    trace: [
      {
        seq: 0,
        tool: "draft_reply",
        cls: "read_class",
        args: {},
        state: "executed",
        draft: {
          body: "made up",
          citations: [],
          faithfulness: { all_grounded: false, claims: [{ claim: "x", label: "CONTRADICTED" }] },
        },
      },
    ],
    actions_pending: [],
    actions_taken: [],
    cost: {},
    draft_reply: { body: "made up", citations: [], faithfulness: { all_grounded: false, claims: [] } },
  };
  const vm = RelayMap.runViewToState(rv);
  const reply = vm.trace[0];
  assert.equal(reply.grounded, false);
  assert.equal(reply.faithLabel, "Not grounded");
  assert.equal(reply.claims[0].label, "CONTRADICTED");
  assert.equal(reply.claims[0].text, "x");
  assert.match(vm.outcome.statusLine, /1 reply \(ungrounded\)/);
});

test("records with only a ticket (no customer) still map", () => {
  const vm = RelayMap.runViewToState({
    status: "done",
    triage: { intent: "spam", priority: "low", confidence: "high", extracted_fields: {} },
    trace: [],
    records: { customer: null, ticket: { id: "T-9", status: "open", queue: null } },
    cost: {},
  });
  assert.ok(vm.records);
  assert.equal(vm.records.ticketId, "#T-9");
  assert.equal(vm.records.queue, "unassigned"); // null queue → display default
  assert.equal(vm.records.email, "");
});

test("records with an empty customer + no ticket → null (nothing touched)", () => {
  const vm = RelayMap.runViewToState({
    status: "done",
    triage: { intent: "spam", priority: "low", confidence: "high", extracted_fields: {} },
    trace: [],
    records: { customer: {}, ticket: null },
    cost: {},
  });
  assert.equal(vm.records, null);
  assert.equal(vm.recordsState, "empty");
});

test("unrecognized step degrades to a read row (no throw)", () => {
  const vm = RelayMap.runViewToState({
    status: "done",
    trace: [{ seq: 0, tool: "mystery", cls: "state_change", args: { a: 1 }, state: "weird" }],
    cost: {},
  });
  assert.equal(vm.trace[0].kind, "read");
  assert.equal(vm.trace[0].tool, "mystery");
});

test("argsLine skips null + nested-object args", () => {
  const line = RelayMap._helpers.argsLine("update_ticket", {
    ticket_id: "T-1",
    status: null,
    fields: { a: 1 },
    note: "ok",
  });
  assert.equal(line, 'ticket_id "T-1" · note "ok"');
});

test("done run with no state-change and no draft → neutral outcome", () => {
  const vm = RelayMap.runViewToState({
    status: "done",
    triage: { intent: "spam", priority: "low", confidence: "high", extracted_fields: {} },
    trace: [{ seq: 0, tool: "lookup_customer", cls: "read", args: {}, state: "executed" }],
    records: { customer: { email: "a@b.com" }, ticket: null },
    cost: {},
  });
  assert.equal(vm.recordsState, "populated");
  assert.equal(vm.outcome.statusLine, "done");
  assert.match(vm.outcome.summary, /Looked up the customer\./);
});
