/**
 * Split 09 — T4: the `send_reply` irreversible gate variant (R3, UI §5.6/§7).
 *
 * A pending `send_reply` is flagged irreversible (the one place red appears) and the gate model
 * carries the **exact reply body + citations** (resolved to their KB text from the draft step) so
 * the operator approves the precise text. A non-`send_reply` pending is never irreversible and
 * carries no reply block.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const RelayMap = require("../map.js");
const fixture = (name) =>
  JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", name), "utf8"));

test("send_reply pending → irreversible + exact reply body + resolved citations", () => {
  const vm = RelayMap.runViewToState(fixture("send_reply_pending.json"));
  const p = vm.pending;
  assert.equal(p.tool, "send_reply");
  assert.equal(p.irreversible, true);
  assert.ok(p.reply, "the gate carries the reply block");
  assert.match(p.reply.body, /duplicate charge/);
  assert.equal(p.reply.citations.length, 1);
  const c = p.reply.citations[0];
  assert.equal(c.chunk_id, "kb-refund-001");
  assert.match(c.text, /Duplicate charges are refunded/); // resolved from the draft step
  assert.equal(c.label, "Billing Policy · #kb-refund-001");
});

test("update_ticket pending is NOT irreversible and carries no reply block", () => {
  const vm = RelayMap.runViewToState(fixture("billing_awaiting.json"));
  assert.equal(vm.pending.tool, "update_ticket");
  assert.equal(vm.pending.irreversible, false);
  assert.equal(vm.pending.reply, null);
});

test("in a multi-pending turn only the send_reply action is irreversible", () => {
  const vm = RelayMap.runViewToState(fixture("multi_pending.json"));
  const byTool = Object.fromEntries(vm.pendings.map((p) => [p.tool, p]));
  assert.equal(byTool.update_ticket.irreversible, false);
  assert.equal(byTool.send_reply.irreversible, true);
  assert.ok(byTool.send_reply.reply.body.length > 0);
});

test("send_reply with an unresolved citation degrades to a chunk-id label (no throw)", () => {
  const rv = {
    status: "awaiting_approval",
    triage: { intent: "billing_dispute", priority: "high", confidence: "medium", extracted_fields: {} },
    trace: [{ seq: 0, tool: "send_reply", cls: "state_change", args: {}, state: "awaiting" }],
    actions_pending: [
      { id: "a1", tool: "send_reply", args: { to: "j@x.com", body: "hello", citations: ["kb-unknown"] }, rationale: "r" },
    ],
    cost: {},
  };
  const p = RelayMap.runViewToState(rv).pending;
  assert.equal(p.reply.citations[0].chunk_id, "kb-unknown");
  assert.equal(p.reply.citations[0].text, ""); // no text available → empty, not a crash
});

test("update_ticket pending carries an 'If approved' diff from the real ticket row", () => {
  const vm = RelayMap.runViewToState(fixture("billing_awaiting.json"));
  const diff = vm.pending.diff;
  assert.equal(diff.field, "status");
  assert.equal(diff.current, "open"); // the ticket's REAL current status, not a hardcoded value
  assert.equal(diff.proposed, "pending_refund");
  assert.equal(diff.ticketId, "T-1042");
});

test("a route_ticket pending shows a queue diff from the real ticket row", () => {
  const rv = {
    status: "awaiting_approval",
    triage: { intent: "technical_issue", priority: "high", confidence: "high", extracted_fields: {} },
    trace: [{ seq: 0, tool: "route_ticket", cls: "state_change", args: {}, state: "awaiting" }],
    actions_pending: [
      { id: "a1", tool: "route_ticket", args: { ticket_id: "T-1050", queue: "tech" }, rationale: "route it" },
    ],
    records: { customer: null, ticket: { id: "T-1050", status: "open", queue: "unassigned" } },
    cost: {},
  };
  const diff = RelayMap.runViewToState(rv).pending.diff;
  assert.equal(diff.field, "queue");
  assert.equal(diff.current, "unassigned");
  assert.equal(diff.proposed, "tech");
});
