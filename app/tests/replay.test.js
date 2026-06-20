/**
 * Split 09 — T3: provider replay uses real per-provider numbers, no synthetic factor (R4).
 *
 * Given two real RunView `cost` objects (anthropic + openai), the cost line renders each provider's
 * real `total_usd` and the tween targets (from → to) are exactly those real numbers — there is no
 * `0.797`-style fabricated provider factor anywhere. The cache caption is honest per provider.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const RelayMap = require("../map.js");
const HTML = fs.readFileSync(path.join(__dirname, "..", "Relay.dc.html"), "utf8");
const MAP = fs.readFileSync(path.join(__dirname, "..", "map.js"), "utf8");

test("replay targets are the two providers' REAL totals (no fabricated factor)", () => {
  // Realistic: gpt-5.5 (a reasoning model) costs materially MORE than Sonnet — not a fixed ratio.
  const anthropic = { provider: "anthropic", cost: { total_usd: 0.0047, tokens: { cache_read: 0 } } };
  const openai = { provider: "openai", cost: { total_usd: 0.029, tokens: {} } };
  const a = RelayMap.runViewToState(anthropic);
  const o = RelayMap.runViewToState(openai);

  assert.equal(a.cost, 0.0047);
  assert.equal(o.cost, 0.029);

  const targets = RelayMap.replayTargets(a, o);
  assert.deepEqual(targets, { from: 0.0047, to: 0.029 });

  // The openai number is an independent real run, not the anthropic number times any factor.
  assert.notEqual(o.cost, +(a.cost * 0.797).toFixed(4));
});

test("replayTargets reads either a raw cost object or a mapped view-model", () => {
  assert.deepEqual(RelayMap.replayTargets({ total_usd: 0.02 }, { total_usd: 0.01 }), {
    from: 0.02,
    to: 0.01,
  });
  assert.deepEqual(RelayMap.replayTargets({ total: 0.02 }, { total: 0.01 }), { from: 0.02, to: 0.01 });
  assert.deepEqual(RelayMap.replayTargets(null, null), { from: 0, to: 0 });
});

test("cache caption is honest per provider on a replay", () => {
  const a = RelayMap.runViewToState({ provider: "anthropic", cost: { tokens: { cache_read: 0 } } });
  const o = RelayMap.runViewToState({ provider: "openai", cost: { tokens: {} } });
  assert.equal(a.cacheCaption, "below cache floor — no benefit (expected)");
  assert.equal(o.cacheCaption, "no prompt cache on the OpenAI path (expected)");
});

test("no 0.797-style synthetic provider factor remains in the frontend (T3, E4)", () => {
  for (const src of [HTML, MAP]) {
    assert.ok(!/0\.797/.test(src), "fabricated 0.797 factor found");
    assert.ok(!/fudge|fakeProvider|synthFactor/i.test(src), "a fake-provider helper leaked");
  }
});
