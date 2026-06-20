/**
 * Split 09 — T2: multi-pending turn-granular batch logic (R2, spec §8).
 *
 * A RunView with 2 `actions_pending` must: list both in the batch sheet model, keep Resume
 * disabled until **both** are decided, and emit a single `/approve` `decisions` array covering
 * **both** (allow+reject mix, with per-action `edited_args`). A decisions set missing one →
 * `buildDecisions` returns `null` so the client cannot send a partial batch (a §8 correctness bug:
 * every `tool_use` block needs a matching `tool_result` before the loop resumes).
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const RelayMap = require("../map.js");

const multi = JSON.parse(
  fs.readFileSync(path.join(__dirname, "fixtures", "multi_pending.json"), "utf8")
);

test("two pending actions both map into the batch sheet model", () => {
  const vm = RelayMap.runViewToState(multi);
  assert.equal(vm.status, "awaiting_approval");
  assert.equal(vm.pendingCount, 2);
  assert.equal(vm.multiPending, true);
  assert.deepEqual(
    vm.pendings.map((p) => p.tool),
    ["update_ticket", "send_reply"]
  );
  // both pending rows are present in the trace as 'awaiting'
  const awaiting = vm.trace.filter((r) => r.kind === "awaiting");
  assert.equal(awaiting.length, 2);
  // the single-pending fast path still points at the first
  assert.equal(vm.pending.id, vm.pendings[0].id);
});

test("Resume stays disabled until EVERY pending action is decided", () => {
  const vm = RelayMap.runViewToState(multi);
  const [a, b] = vm.pendings;
  assert.equal(RelayMap.allDecided(vm.pendings, {}), false);
  assert.equal(RelayMap.allDecided(vm.pendings, { [a.id]: { verb: "allow" } }), false);
  assert.equal(
    RelayMap.allDecided(vm.pendings, { [a.id]: { verb: "allow" }, [b.id]: { verb: "reject" } }),
    true
  );
});

test("a partial batch cannot be sent (buildDecisions → null)", () => {
  const vm = RelayMap.runViewToState(multi);
  const [a] = vm.pendings;
  assert.equal(RelayMap.buildDecisions(vm.pendings, { [a.id]: { verb: "allow" } }), null);
});

test("a full allow+reject batch builds the /approve decisions array (with edited_args)", () => {
  const vm = RelayMap.runViewToState(multi);
  const [a, b] = vm.pendings;
  const decisions = RelayMap.buildDecisions(vm.pendings, {
    [a.id]: { verb: "allow", editedArgs: { status: "refunded" } },
    [b.id]: { verb: "reject" },
  });
  assert.deepEqual(decisions, [
    { approval_id: a.id, decision: "allow", edited_args: { status: "refunded" } },
    { approval_id: b.id, decision: "reject" },
  ]);
});

test("allow with no edits omits edited_args", () => {
  const vm = RelayMap.runViewToState(multi);
  const [a, b] = vm.pendings;
  const decisions = RelayMap.buildDecisions(vm.pendings, {
    [a.id]: { verb: "allow" },
    [b.id]: { verb: "allow", editedArgs: {} },
  });
  assert.ok(decisions.every((d) => !("edited_args" in d)));
});

test("single-pending is the common-case fast path (not multiPending)", () => {
  const single = JSON.parse(
    fs.readFileSync(path.join(__dirname, "fixtures", "billing_awaiting.json"), "utf8")
  );
  const vm = RelayMap.runViewToState(single);
  assert.equal(vm.pendingCount, 1);
  assert.equal(vm.multiPending, false);
  assert.ok(vm.pending);
});
