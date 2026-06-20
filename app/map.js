/**
 * RunView → view-model mapper (Split 08 R3, extended in Split 09) — the heart of the split.
 *
 * `runViewToState(runView, opts)` is a **pure, total** function that turns the backend's frozen
 * RunView (Split 07) into exactly the shapes the existing `renderVals()` already consumes — triage,
 * the ordered trace rows, the records panel, the gate `pending(s)`, and the cost block. It maps the
 * data source; it never rebuilds the UI. Unknown/missing optional fields degrade gracefully (no
 * throw), so a partial or error RunView still renders.
 *
 * Split 09 extends it to every §20/§9 edge state — refusal, step-cap, `is_error`, ambiguous, spam,
 * below-cache-floor, error status — plus **multi-pending** (all `actions_pending`, not just `[0]`),
 * the `send_reply` irreversible variant (body + citations), and honest per-provider cache/cost.
 * Every state is read from a **real RunView/error field**, never a simulated client flag (E6).
 * Markers the RunView does not yet carry (`refusal`/`step_capped`/step `is_error`) are consumed
 * *if present* and otherwise absent — see the Split-09 carry-forwards for the Split-07 amendment.
 *
 * Dependency-free; runs in the browser (as `window.RelayMap`) and under Node's test runner.
 */
(function (root, factory) {
  var mod = factory();
  if (typeof module === "object" && module.exports) module.exports = mod;
  else root.RelayMap = mod;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // Args the operator must not edit at the gate (an id is the write's target, not a value).
  var READONLY_ARG_KEYS = { ticket_id: true, customer_id: true };

  // ---- small helpers -------------------------------------------------------

  function isObj(v) {
    return v !== null && typeof v === "object";
  }

  /** A one-line `key "value" · key "value"` rendering of a tool's args (search_kb shows the query). */
  function argsLine(tool, args) {
    if (!isObj(args)) return "";
    if (tool === "search_kb" && args.query != null) return '"' + args.query + '"';
    var parts = [];
    Object.keys(args).forEach(function (k) {
      var v = args[k];
      if (v === null || v === undefined || isObj(v)) return;
      parts.push(k + ' "' + v + '"');
    });
    return parts.join(" · ");
  }

  function latencyText(ms) {
    return ms === null || ms === undefined ? "" : ms + "ms";
  }

  /** Thousands-separated integer (matches the component's `fmt`). */
  function fmtNum(n) {
    return String(n || 0).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function isGrounded(faith) {
    return !!(faith && faith.all_grounded);
  }

  function faithLabelOf(faith) {
    if (!faith) return "";
    var n = (faith.claims || []).length;
    return faith.all_grounded ? "Grounded (" + n + "/" + n + ")" : "Not grounded";
  }

  // ---- triage --------------------------------------------------------------

  function mapTriage(triage) {
    if (!triage) return null;
    var ef = triage.extracted_fields || {};
    var pick = function (k) {
      return ef[k] === undefined ? null : ef[k];
    };
    return {
      intent: triage.intent,
      priority: triage.priority,
      confidence: triage.confidence,
      fields: {
        customer_email: pick("customer_email"),
        order_ref: pick("order_ref"),
        amount: pick("amount"),
        product: pick("product"),
      },
    };
  }

  // ---- trace rows ----------------------------------------------------------

  function mapCitations(citations) {
    return (citations || []).map(function (c) {
      var source = c.source || "";
      return {
        chunk_id: c.chunk_id,
        label: source ? source + " · #" + c.chunk_id : "#" + c.chunk_id,
        text: c.text || "",
        url: c.url || "",
      };
    });
  }

  function mapClaims(faith) {
    return ((faith && faith.claims) || []).map(function (cl) {
      return { label: cl.label, text: cl.claim };
    });
  }

  function replyRow(step) {
    var draft = step.draft || {};
    var faith = draft.faithfulness || null;
    var grounded = isGrounded(faith);
    return {
      kind: "reply",
      glyph: grounded ? "check" : "alert",
      tool: "draft_reply",
      replyBody: draft.body || "",
      streaming: false,
      citations: mapCitations(draft.citations),
      claims: mapClaims(faith),
      grounded: grounded,
      faithLabel: faith ? faithLabelOf(faith) : "",
      // §5.5: an ungrounded draft shows the outline alert + "model may revise" (it is fed back to
      // the model, never blocked — faithfulness is not a gate input).
      mayRevise: !!faith && !grounded,
    };
  }

  function executedStateChangeRow(step) {
    if (step.decision === "approved") {
      return { kind: "approved", glyph: "check", tool: step.tool, result: "approved · by you" };
    }
    // auto-executed by policy (route_ticket / escalate under the default policy)
    return {
      kind: "auto",
      glyph: "auto",
      tool: step.tool,
      argsLine: argsLine(step.tool, step.args),
      result: step.tool + " auto-approved by policy",
    };
  }

  /** A backend tool error on a trace step (§20 "backend conflict / missing record"). The engine
   * does not yet mark errored steps in the trace (it writes no `tool_calls` row on a `ToolError`),
   * so this fires only when a future RunView carries `step.is_error` — see the Split-09
   * carry-forward. Read from a real field; never fabricated. */
  function errorLineOf(step) {
    if (!step.is_error) return "";
    return step.error || step.result_summary || "backend error — model will adapt";
  }

  function mapStep(step) {
    var tool = step.tool;
    var errLine = errorLineOf(step);
    if (tool === "draft_reply") return replyRow(step);
    if (step.cls === "read" || step.cls === "read_class") {
      return {
        kind: "read",
        glyph: errLine ? "alert" : "check",
        tool: tool,
        argsLine: argsLine(tool, step.args),
        result: step.result_summary || "",
        latency: latencyText(step.latency_ms),
        isError: !!errLine,
        errorLine: errLine,
      };
    }
    // state_change
    if (step.state === "executed") return executedStateChangeRow(step);
    if (step.state === "awaiting") return { kind: "awaiting", glyph: "arrow", tool: tool };
    if (step.state === "rejected") {
      return { kind: "rejected", glyph: "cross", tool: tool, result: "rejected — no change" };
    }
    if (step.state === "blocked") {
      return { kind: "blocked", glyph: "cross", tool: tool, result: "blocked by policy" };
    }
    // graceful fallback for an unrecognized step
    return {
      kind: "read",
      glyph: "check",
      tool: tool,
      argsLine: argsLine(tool, step.args),
      result: step.result_summary || "",
    };
  }

  // ---- records -------------------------------------------------------------

  function firstFlag(flags) {
    if (!flags) return null;
    var keys = Object.keys(flags);
    for (var i = 0; i < keys.length; i++) {
      if (flags[keys[i]]) return keys[i];
    }
    return null;
  }

  function mapRecords(records) {
    if (!records) return null;
    var c = records.customer || {};
    var t = records.ticket || {};
    var hasCustomer = records.customer && (c.email || c.plan || c.status);
    var hasTicket = records.ticket && t.id;
    if (!hasCustomer && !hasTicket) return null;
    return {
      email: c.email || "",
      plan: c.plan || "",
      status: c.status || "",
      flag: firstFlag(c.flags),
      ticketId: t.id ? "#" + String(t.id).replace(/^#/, "") : "",
      ticketStatus: t.status || "",
      queue: t.queue || "unassigned",
    };
  }

  function proposedStatusOf(records) {
    var p = records && records.proposed;
    return p && p.field === "status" ? p.proposed || null : null;
  }

  function recordsStateOf(runView, records) {
    if (!records) return "empty";
    if (runView.status === "awaiting_approval") {
      return runView.records && runView.records.proposed ? "proposed" : "populated";
    }
    if (runView.status === "done") {
      var sc = (runView.trace || []).filter(function (s) {
        return s.cls === "state_change";
      });
      var committed = sc.some(function (s) {
        return s.decision === "approved" || s.decision === "auto";
      });
      if (committed) return "committed";
      if (sc.some(function (s) { return s.state === "rejected"; })) return "nochange";
      if (sc.some(function (s) { return s.state === "blocked"; })) return "blocked";
      return "populated";
    }
    return "populated";
  }

  // ---- gate / pending ------------------------------------------------------

  function mapPendingArgs(argsObj) {
    var keys = Object.keys(argsObj).filter(function (k) {
      var v = argsObj[k];
      return v !== null && v !== undefined && !isObj(v);
    });
    // ticket_id first so the gate's diff line (which reads args[0]) names the ticket.
    keys.sort(function (a, b) {
      if (a === "ticket_id") return -1;
      if (b === "ticket_id") return 1;
      return 0;
    });
    return keys.map(function (k) {
      return { key: k, value: String(argsObj[k]), editable: !READONLY_ARG_KEYS[k] };
    });
  }

  /** Resolve cited `chunk_id`s to their enriched `{chunk_id, text, source, url}` from any
   * `draft_reply` trace step (the only place the RunView carries citation text). Used to show the
   * exact reply + citations in a pending `send_reply` gate (R3). */
  function resolvedCitations(runView) {
    var lookup = {};
    (runView.trace || []).forEach(function (s) {
      ((s.draft && s.draft.citations) || []).forEach(function (c) {
        if (c && c.chunk_id) lookup[c.chunk_id] = c;
      });
    });
    return lookup;
  }

  /** The pending write's plain-language "If approved" diff, read from the ticket's *real* current
   * row (records), never a hardcoded "open" (UI §5.6). Returns `null` for `send_reply` (no ticket
   * diff — the reply body is shown instead). */
  function pendingDiff(args, records) {
    var ticketId = args.ticket_id != null ? String(args.ticket_id) : "";
    var ticket = (records && records.ticket) || {};
    var current = function (field) {
      return ticket.id && String(ticket.id) === ticketId ? ticket[field] : null;
    };
    if (args.status != null) {
      return { ticketId: ticketId, field: "status", current: current("status"), proposed: String(args.status) };
    }
    if (args.queue != null) {
      return { ticketId: ticketId, field: "queue", current: current("queue"), proposed: String(args.queue) };
    }
    return null;
  }

  function pendingReply(tool, args, citeLookup) {
    if (tool !== "send_reply") return null;
    var citeIds = args.citations || [];
    return {
      body: args.body != null ? String(args.body) : "",
      citations: mapCitations(
        (Array.isArray(citeIds) ? citeIds : []).map(function (id) {
          return citeLookup[id] || { chunk_id: id };
        })
      ),
    };
  }

  function mapOnePending(ar, records, citeLookup, injectionHint) {
    var args = ar.args || {};
    return {
      id: ar.id,
      tool: ar.tool,
      args: mapPendingArgs(args),
      rationale: ar.rationale || "",
      proposedStatus: args.status != null ? String(args.status) : null,
      diff: pendingDiff(args, records),
      irreversible: ar.tool === "send_reply",
      reply: pendingReply(ar.tool, args, citeLookup),
      // R1 injection dark-beat: the gate held on a ticket that tried to force an un-approved
      // action. Derived from the locked `/examples` flag (a real backend signal), not fabricated —
      // see the Split-09 carry-forward for a first-class RunView injection hint.
      injection: !!injectionHint,
    };
  }

  /** All pending actions of the suspended turn (R2 multi-pending), not just `[0]`. */
  function mapPendings(runView, opts) {
    var pendings = runView.actions_pending || [];
    if (!pendings.length) return [];
    var records = runView.records;
    var citeLookup = resolvedCitations(runView);
    var injectionHint = opts && opts.injectionHint;
    return pendings.map(function (ar) {
      return mapOnePending(ar, records, citeLookup, injectionHint);
    });
  }

  // ---- cost ----------------------------------------------------------------

  /** The honest cache caption (§5.7) — never an error. OpenAI has no prompt cache on this path; on
   * Anthropic, a real cache hit reads "saved" tokens, else "below cache floor" (a benign expected
   * state, not a failure — the stable prefix is under Sonnet's 2048-token floor, Split 02). */
  function cacheCaption(provider, tokens) {
    tokens = tokens || {};
    if (provider === "openai") return "no prompt cache on the OpenAI path (expected)";
    var cr = tokens.cache_read || 0;
    if (cr > 0) return "cache_read " + fmtNum(cr) + " tokens reused";
    return "below cache floor — no benefit (expected)";
  }

  function mapCost(cost, provider) {
    cost = cost || {};
    var tk = cost.tokens || {};
    var tokens = {
      in: tk.input || 0,
      out: tk.output || 0,
      cache_read: tk.cache_read || 0,
      cache_creation: tk.cache_creation || 0,
    };
    return {
      total: cost.total_usd || 0,
      breakdown: (cost.by_call || []).map(function (c) {
        return { kind: c.kind, cost: c.cost_usd };
      }),
      tokens: tokens,
      latency: cost.latency_s ? Number(cost.latency_s).toFixed(1) + "s" : "",
      cacheCaption: cacheCaption(provider, tk),
      belowCacheFloor: provider !== "openai" && !(tk.cache_read || 0),
    };
  }

  // ---- outcome (composed from the ledger, not the model's prose, §20) ------

  function actionVerb(step) {
    var args = step.args || {};
    var dec = step.decision || step.state;
    var suffix = dec ? " (" + dec + ")" : "";
    if (step.tool === "update_ticket") {
      return "marked the ticket " + (args.status || "updated") + suffix;
    }
    if (step.tool === "route_ticket") {
      return "routed the ticket to " + (args.queue || "a queue") + suffix;
    }
    if (step.tool === "escalate") return "escalated to " + (args.level || "review") + suffix;
    if (step.tool === "send_reply") return "sent the reply" + suffix;
    return step.tool + suffix;
  }

  function composeOutcome(runView) {
    if (runView.status !== "done") return null;
    var trace = runView.trace || [];
    var draft = runView.draft_reply || null;
    var sc = trace.filter(function (s) {
      return s.cls === "state_change";
    });

    var parts = ["done"];
    if (draft) parts.push("1 reply (" + (isGrounded(draft.faithfulness) ? "grounded" : "ungrounded") + ")");
    var counts = {};
    sc.forEach(function (s) {
      var key = s.decision || s.state;
      counts[key] = (counts[key] || 0) + 1;
    });
    ["approved", "auto", "rejected", "blocked"].forEach(function (k) {
      if (counts[k]) parts.push(counts[k] + " " + k);
    });

    var sentences = [];
    if (trace.some(function (s) { return s.tool === "lookup_customer"; })) {
      sentences.push("Looked up the customer");
    }
    if (draft) sentences.push("drafted a cited reply");
    sc.forEach(function (s) {
      sentences.push(actionVerb(s));
    });
    var summary = sentences.length ? sentences.join(", ") + "." : "Run complete.";
    summary = summary.charAt(0).toUpperCase() + summary.slice(1);
    return { summary: summary, statusLine: parts.join(" · ") };
  }

  // ---- edge-state notices + triage hints (§20 / §9 edge table) -------------

  /** Short, neutral captions for the triage card (color is never the only signal, §9). */
  function triageHints(triage) {
    if (!triage) return [];
    var hints = [];
    if (triage.confidence === "low") hints.push("low → biases to route / escalate");
    if (triage.intent === "spam") hints.push("intent: spam — no action warranted");
    return hints;
  }

  /** Whether the run resolved by routing/escalating with no pending write (UI §9 "ambiguous"). */
  function isAmbiguousNoWrite(runView, pendings) {
    if (runView.status !== "done" || pendings.length) return false;
    var sc = (runView.trace || []).filter(function (s) {
      return s.cls === "state_change";
    });
    if (!sc.length) return false;
    return sc.every(function (s) {
      return s.tool === "route_ticket" || s.tool === "escalate";
    });
  }

  /** Run-level notices (terminal/edge captions). Each is read from a real RunView field; the
   * `refusal`/`step_capped` markers are consumed *if present* — the engine does not yet emit them
   * (Split-09 carry-forward: amend the Split-07 contract). Surfaced, never crashed (§20). */
  function buildNotices(runView, pendings) {
    var notices = [];
    if (runView.refusal) {
      var reason = isObj(runView.refusal) ? runView.refusal.reason || "" : "";
      notices.push({
        kind: "refusal",
        text: "Model declined to classify / refused — surfaced, not executed.",
        detail: reason,
      });
    }
    if (runView.step_capped) {
      notices.push({
        kind: "step_cap",
        text: "Reached step cap (6) — stopping; actions left for review.",
        detail: "",
      });
    }
    if (runView.status === "error") {
      notices.push({
        kind: "error",
        text: "Run ended with an error — partial cost still shown (you paid for what ran).",
        detail: runView.error || "",
      });
    }
    if (isAmbiguousNoWrite(runView, pendings)) {
      notices.push({
        kind: "ambiguous",
        text: "Ambiguous ticket — routed / escalated for a human; 0 writes proposed.",
        detail: "",
      });
    }
    return notices;
  }

  // ---- multi-pending decision payload (R2 turn-granular batch) -------------

  /** Every pending action has a decision (`allow`/`reject`). The client refuses to resume until
   * this holds — a partial batch is a §8 correctness bug (some `tool_use` blocks would lack a
   * `tool_result`). `choices` is `{ [id]: { verb, editedArgs? } }`. */
  function allDecided(pendings, choices) {
    choices = choices || {};
    if (!pendings || !pendings.length) return false;
    return pendings.every(function (p) {
      var c = choices[p.id];
      return c && (c.verb === "allow" || c.verb === "reject");
    });
  }

  /** Build the `/approve` `decisions` array covering **every** pending action (allow/reject mix,
   * per-action `edited_args`). Returns `null` if any decision is missing (the client must not send
   * a partial batch). */
  function buildDecisions(pendings, choices) {
    if (!allDecided(pendings, choices)) return null;
    return pendings.map(function (p) {
      var c = choices[p.id];
      var d = { approval_id: p.id, decision: c.verb === "allow" ? "allow" : "reject" };
      if (c.verb === "allow" && c.editedArgs && Object.keys(c.editedArgs).length) {
        d.edited_args = c.editedArgs;
      }
      return d;
    });
  }

  // ---- provider replay (R4) — real per-provider numbers, no synthetic factor

  /** The cost-line tween targets for a provider replay: the previous run's real total → the next
   * run's real total (UI §6 Beat 7). Both come from real `RunView.cost.total_usd` — there is no
   * fabricated cross-provider cost factor; each number is an independent real run (T3). */
  function replayTargets(prevCost, nextCost) {
    var read = function (c) {
      if (typeof c === "number") return c;
      if (!c) return 0;
      if (c.cost !== undefined) return c.cost || 0; // a mapped view-model
      if (c.total !== undefined) return c.total || 0; // a mapped cost block
      return c.total_usd || 0; // a raw RunView cost object
    };
    return { from: read(prevCost), to: read(nextCost) };
  }

  // ---- config view (drives the config sheet + composer, all from /config) --

  function providersFrom(config) {
    return (config && config.providers) || ["anthropic", "openai"];
  }
  function modelsFor(config, provider) {
    return (config && config.models_by_provider && config.models_by_provider[provider]) || [];
  }
  function policiesFrom(config) {
    return (config && config.policies) || ["auto", "default", "strict"];
  }
  function defaultModelFor(config, provider) {
    return (
      (config && config.default_model_by_provider && config.default_model_by_provider[provider]) ||
      ""
    );
  }

  // ---- entry point ---------------------------------------------------------

  function runViewToState(runView, opts) {
    runView = runView || {};
    var trace = (runView.trace || []).map(mapStep);
    var pendingRowIndex = null;
    for (var i = 0; i < trace.length; i++) {
      if (trace[i].kind === "awaiting") {
        pendingRowIndex = i;
        break;
      }
    }
    var records = mapRecords(runView.records);
    var cost = mapCost(runView.cost, runView.provider);
    var pendings = mapPendings(runView, opts);
    return {
      run_id: runView.run_id || runView.id || null,
      status: runView.status || null,
      ticket_id: runView.ticket_id || null,
      provider: runView.provider || null,
      model: runView.model || null,
      triage: mapTriage(runView.triage),
      triageHints: triageHints(runView.triage),
      trace: trace,
      // single-pending fast path (unchanged shape) + the full list for the batch gate (R2).
      pending: pendings[0] || null,
      pendings: pendings,
      multiPending: pendings.length > 1,
      pendingCount: pendings.length,
      pendingRowIndex: pendingRowIndex,
      records: records,
      recordsState: recordsStateOf(runView, records),
      proposedStatus: proposedStatusOf(runView.records),
      cost: cost.total,
      costBreakdown: cost.breakdown,
      tokens: cost.tokens,
      latency: cost.latency,
      cacheCaption: cost.cacheCaption,
      belowCacheFloor: cost.belowCacheFloor,
      notices: buildNotices(runView, pendings),
      outcome: composeOutcome(runView),
    };
  }

  return {
    runViewToState: runViewToState,
    providersFrom: providersFrom,
    modelsFor: modelsFor,
    policiesFrom: policiesFrom,
    defaultModelFor: defaultModelFor,
    cacheCaption: cacheCaption,
    allDecided: allDecided,
    buildDecisions: buildDecisions,
    replayTargets: replayTargets,
    // exposed for unit tests
    _helpers: {
      argsLine: argsLine,
      mapTriage: mapTriage,
      mapStep: mapStep,
      mapRecords: mapRecords,
      recordsStateOf: recordsStateOf,
      mapPendings: mapPendings,
      mapCost: mapCost,
      composeOutcome: composeOutcome,
      triageHints: triageHints,
      buildNotices: buildNotices,
      pendingDiff: pendingDiff,
      isAmbiguousNoWrite: isAmbiguousNoWrite,
    },
  };
});
