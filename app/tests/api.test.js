/**
 * Split 08 — T2: API client error shaping + transport.
 *
 * Drives the client with an injected fake `fetch` (no network): a canned missing-key envelope
 * surfaces the banner string + env var; a 404 `run_not_found` surfaces a clean error; a dead
 * server surfaces a network error — none leak an unhandled rejection. Happy-path calls return the
 * parsed body and send the right method/headers/body.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const RelayApi = require("../api.js");

/** A fake `fetch` returning a canned (status, json) once, recording the call. */
function fakeFetch(status, json, sink) {
  return function (url, init) {
    if (sink) sink.calls.push({ url, init });
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: function () {
        return json instanceof Error ? Promise.reject(json) : Promise.resolve(json);
      },
    });
  };
}

test("getConfig: returns the parsed body on 200", async () => {
  const sink = { calls: [] };
  const body = { providers: ["anthropic", "openai"] };
  const client = RelayApi.makeClient({ fetch: fakeFetch(200, body, sink) });
  const out = await client.getConfig();
  assert.deepEqual(out, body);
  assert.equal(sink.calls[0].url, "/config");
  assert.equal(sink.calls[0].init.method, "GET");
});

test("handle: POSTs JSON with the right method/headers/body", async () => {
  const sink = { calls: [] };
  const client = RelayApi.makeClient({ fetch: fakeFetch(200, { status: "done" }, sink) });
  await client.handle({ ticket: "hi", provider: "openai", policy: "strict" });
  const call = sink.calls[0];
  assert.equal(call.url, "/handle");
  assert.equal(call.init.method, "POST");
  assert.equal(call.init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(call.init.body), {
    ticket: "hi",
    provider: "openai",
    policy: "strict",
  });
});

test("missing-key envelope (424) → banner string + env var", async () => {
  const envelope = {
    error: {
      type: "missing_key",
      message: "ANTHROPIC_API_KEY is not set",
      provider: "anthropic",
      env_var: "ANTHROPIC_API_KEY",
      retriable: false,
    },
  };
  const client = RelayApi.makeClient({ fetch: fakeFetch(424, envelope) });
  await assert.rejects(
    () => client.handle({ ticket: "x", provider: "anthropic" }),
    (err) => {
      assert.equal(err.name, "RelayApiError");
      assert.equal(err.type, "missing_key");
      assert.equal(err.isMissingKey, true);
      assert.equal(err.env_var, "ANTHROPIC_API_KEY");
      assert.equal(err.status, 424);
      assert.match(err.bannerText, /ANTHROPIC_API_KEY is not set/);
      return true;
    }
  );
});

test("missing-key banner appends env var when the message omits it", async () => {
  const envelope = {
    error: { type: "missing_key", message: "key missing", env_var: "OPENAI_API_KEY" },
  };
  const client = RelayApi.makeClient({ fetch: fakeFetch(424, envelope) });
  await assert.rejects(
    () => client.handle({ ticket: "x" }),
    (err) => {
      assert.equal(err.bannerText, "key missing (set OPENAI_API_KEY)");
      return true;
    }
  );
});

test("404 run_not_found → a clean surfaced error", async () => {
  const envelope = {
    error: { type: "run_not_found", message: "no run found for id 'x' (lost or expired)" },
  };
  const client = RelayApi.makeClient({ fetch: fakeFetch(404, envelope) });
  await assert.rejects(
    () => client.approve({ run_id: "x", decisions: [] }),
    (err) => {
      assert.equal(err.type, "run_not_found");
      assert.equal(err.status, 404);
      assert.match(err.bannerText, /no run found/);
      return true;
    }
  );
});

test("dead server (fetch rejects) → a network error, no unhandled rejection", async () => {
  const client = RelayApi.makeClient({
    fetch: function () {
      return Promise.reject(new TypeError("Failed to fetch"));
    },
  });
  await assert.rejects(
    () => client.health(),
    (err) => {
      assert.equal(err.type, "network");
      assert.equal(err.status, 0);
      assert.equal(err.retriable, true);
      assert.match(err.bannerText, /Cannot reach the Relay API/);
      return true;
    }
  );
});

test("error with an unparseable body still shapes cleanly", async () => {
  const client = RelayApi.makeClient({
    fetch: fakeFetch(500, new Error("not json")),
  });
  await assert.rejects(
    () => client.getExamples(),
    (err) => {
      assert.equal(err.name, "RelayApiError");
      assert.equal(err.status, 500);
      assert.ok(err.bannerText.length > 0);
      return true;
    }
  );
});

test("approve: POSTs the decisions body", async () => {
  const sink = { calls: [] };
  const client = RelayApi.makeClient({ fetch: fakeFetch(200, { status: "done" }, sink) });
  await client.approve({
    run_id: "run_1",
    decisions: [{ approval_id: "a1", decision: "allow" }],
  });
  const call = sink.calls[0];
  assert.equal(call.url, "/approve");
  assert.equal(call.init.method, "POST");
  assert.deepEqual(JSON.parse(call.init.body).decisions, [
    { approval_id: "a1", decision: "allow" },
  ]);
});

test("baseUrl is honored", async () => {
  const sink = { calls: [] };
  const client = RelayApi.makeClient({
    baseUrl: "http://localhost:8000",
    fetch: fakeFetch(200, {}, sink),
  });
  await client.health();
  assert.equal(sink.calls[0].url, "http://localhost:8000/health");
});
