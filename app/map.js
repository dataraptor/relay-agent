/**
 * RunView → view-model mapper (Split 08 R3) — the heart of the split.
 *
 * `runViewToState(runView)` is a **pure, total** function that turns the backend's frozen RunView
 * (Split 07) into exactly the shapes the existing `renderVals()` already consumes — triage, the
 * ordered trace rows, the records panel, the gate `pending`, and the cost block. It maps the data
 * source; it never rebuilds the UI. Unknown/missing optional fields degrade gracefully (no throw),
 * so a partial or error RunView still renders.
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
    return {
      kind: "reply",
      glyph: "check",
      tool: "draft_reply",
      replyBody: draft.body || "",
      streaming: false,
      citations: mapCitations(draft.citations),
      claims: mapClaims(faith),
      grounded: isGrounded(faith),
      faithLabel: faith ? faithLabelOf(faith) : "",
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

  function mapStep(step) {
    var tool = step.tool;
    if (tool === "draft_reply") return replyRow(step);
    if (step.cls === "read" || step.cls === "read_class") {
      return {
        kind: "read",
        glyph: "check",
        tool: tool,
        argsLine: argsLine(tool, step.args),
        result: step.result_summary || "",
        latency: latencyText(step.latency_ms),
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

  function mapPending(runView) {
    var pendings = runView.actions_pending || [];
    if (!pendings.length) return null;
    var ar = pendings[0];
    var args = ar.args || {};
    return {
      id: ar.id,
      tool: ar.tool,
      args: mapPendingArgs(args),
      rationale: ar.rationale || "",
      proposedStatus: args.status != null ? String(args.status) : null,
      irreversible: ar.tool === "send_reply",
      injection: false, // detecting injection from a RunView is a Split 09 concern
    };
  }

  // ---- cost ----------------------------------------------------------------

  function mapCost(cost) {
    cost = cost || {};
    var tk = cost.tokens || {};
    return {
      total: cost.total_usd || 0,
      breakdown: (cost.by_call || []).map(function (c) {
        return { kind: c.kind, cost: c.cost_usd };
      }),
      tokens: {
        in: tk.input || 0,
        out: tk.output || 0,
        cache_read: tk.cache_read || 0,
        cache_creation: tk.cache_creation || 0,
      },
      latency: cost.latency_s ? Number(cost.latency_s).toFixed(1) + "s" : "",
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

  function runViewToState(runView) {
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
    var cost = mapCost(runView.cost);
    return {
      run_id: runView.run_id || runView.id || null,
      status: runView.status || null,
      ticket_id: runView.ticket_id || null,
      triage: mapTriage(runView.triage),
      trace: trace,
      pending: mapPending(runView),
      pendingRowIndex: pendingRowIndex,
      records: records,
      recordsState: recordsStateOf(runView, records),
      proposedStatus: proposedStatusOf(runView.records),
      cost: cost.total,
      costBreakdown: cost.breakdown,
      tokens: cost.tokens,
      latency: cost.latency,
      outcome: composeOutcome(runView),
    };
  }

  return {
    runViewToState: runViewToState,
    providersFrom: providersFrom,
    modelsFor: modelsFor,
    policiesFrom: policiesFrom,
    defaultModelFor: defaultModelFor,
    // exposed for unit tests
    _helpers: {
      argsLine: argsLine,
      mapTriage: mapTriage,
      mapStep: mapStep,
      mapRecords: mapRecords,
      recordsStateOf: recordsStateOf,
      mapPending: mapPending,
      mapCost: mapCost,
      composeOutcome: composeOutcome,
    },
  };
});
