"""The manual tool-use loop, the approval gate's enforcement, and suspend/resume (spec §5–§8, §11).

This module is the trust story. It runs a **manual** tool-use loop (never the SDK auto
tool-runner — that would bypass the gate, §4/§21), intercepting every proposed tool call with
:class:`relay.gate.Gate` *before* execution. State-changing actions either auto-execute (policy
``auto``), **pause** for human approval (policy ``ask``), or are **blocked** (policy ``deny``).

``handle()`` and ``approve()`` are **two separate process invocations** in the CLI, so the run
must live in a **durable, run_id-addressable file DB** (R3): ``handle`` persists the in-flight
transcript to ``<state_dir>/runs/<run_id>.db`` and ``approve`` reopens it by id. (The ephemeral
``:memory:`` mode from Split 01 stays available for the in-process eval/test path that never
suspends across processes.)

Everything authoritative comes from the **ledger**, never the model's prose (§20):
``actions_taken`` is built from ``actions_log``; ``cost_usd`` is ``SUM(llm_calls.cost_usd)``.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from collections import Counter
from typing import Any

from . import faithfulness
from .backend import db
from .cost import Usage, compute_cost
from .gate import STATE_CHANGE_TOOLS, Gate, GateAction, build_policy
from .models import (
    ActionResult,
    ApprovalRequest,
    Citation,
    Decision,
    DraftReply,
    Faithfulness,
    Outcome,
    ToolClass,
    Triage,
)
from .prompts import PROMPT_VERSION, TRIAGE_SYSTEM, agent_first_user_content, triage_user_content
from .provider.base import ModelStep, ProviderClient
from .tools import ToolError, tool_schemas
from .tools import execute as execute_tool

__all__ = ["handle", "approve", "assert_no_unapproved_writes", "make_provider", "MAX_TOOL_CALLS"]

#: Bounded loop — cap on tool calls per ticket to cap cost/latency (§6 "≤6 tool calls").
MAX_TOOL_CALLS = 6


# ---------------------------------------------------------------------------
# Provider construction (shared with relay.triage)
# ---------------------------------------------------------------------------


def make_provider(provider: str, model: str | None) -> ProviderClient:
    """Construct a real provider by name. OpenAI arrives in Split 05.

    The ``StubProvider`` is never built here — tests inject it directly via the private
    ``_provider`` argument of :func:`handle` / :func:`approve`.
    """
    if provider == "anthropic":
        from .provider.anthropic import AnthropicProvider

        return AnthropicProvider(model=model) if model else AnthropicProvider()
    if provider == "openai":
        raise NotImplementedError("the OpenAI provider is built in Split 05")
    raise ValueError(f"unknown provider {provider!r}")


# ---------------------------------------------------------------------------
# Run store (durable, run_id-addressable — load-bearing for cross-process approve, R3)
# ---------------------------------------------------------------------------


def _state_dir(store_dir: str | None) -> str:
    if store_dir:
        return store_dir
    return os.environ.get("RELAY_STATE_DIR") or os.path.join(tempfile.gettempdir(), "relay-runs")


def _run_db_path(run_id: str, store_dir: str | None) -> str:
    """Per-run file DB path: ``<state_dir>/runs/<run_id>.db`` (configurable via arg/env)."""
    return os.path.join(_state_dir(store_dir), "runs", f"{run_id}.db")


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# Wire-format helpers (the transcript is Anthropic-native message dicts; the OpenAI
# provider will translate at its own seam in Split 05 — see Notes for next session)
# ---------------------------------------------------------------------------


def _assistant_turn(step: ModelStep) -> dict[str, Any]:
    """Reconstruct the assistant turn (text + tool_use blocks) for the persisted transcript."""
    content: list[dict[str, Any]] = []
    if step.text:
        content.append({"type": "text", "text": step.text})
    for tc in step.tool_calls:
        content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.args})
    return {"role": "assistant", "content": content}


def _tool_result_block(tool_use_id: str, result: Any, is_error: bool) -> dict[str, Any]:
    """A ``tool_result`` block. Every ``tool_use`` block must get one before resuming (API rule)."""
    content = result if isinstance(result, str) else json.dumps(result)
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


# ---------------------------------------------------------------------------
# Cost / ledger helpers
# ---------------------------------------------------------------------------


def _price_call(provider: str, model: str, usage: Usage) -> float:
    """USD for one inference. The ``stub`` provider has no real cost (its tokens are fake)."""
    if provider == "stub":
        return 0.0
    return compute_cost(provider, model, usage)


def _record_llm_call(
    conn: Any, run_id: str, ticket_id: str | None, kind: str, provider: ProviderClient, usage: Usage
) -> None:
    db.insert_llm_call(
        conn,
        run_id=run_id,
        ticket_id=ticket_id,
        kind=kind,
        provider=provider.provider,
        model=provider.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        cost_usd=_price_call(provider.provider, provider.model, usage),
    )


def _run_tool(
    conn: Any, run_id: str, ticket_id: str | None, step_idx: int, name: str, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Execute a tool against the backend. Writes a ``tool_calls`` row **only on success**
    (a real mutation/read happened); on a :class:`ToolError` returns its feed-back payload with
    ``is_error=True`` and writes no row. Returns ``(result, is_error)``."""
    t0 = time.perf_counter()
    try:
        result = execute_tool(name, args, conn)
    except ToolError as exc:
        return exc.to_result(), True
    db.insert_tool_call(
        conn,
        run_id=run_id,
        ticket_id=ticket_id,
        step=step_idx,
        tool=name,
        args_json=json.dumps(args),
        result_json=json.dumps(result),
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    return result, False


# ---------------------------------------------------------------------------
# The manual loop core (shared by handle() and approve()'s resume)
# ---------------------------------------------------------------------------


def _drive(
    conn: Any,
    run_id: str,
    provider: ProviderClient,
    gate: Gate,
    messages: list[dict[str, Any]],
    used: int,
    ticket_id: str | None,
    draft_state: dict[str, Any] | None,
    policy_map: dict[str, str],
    triage: Triage,
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """Run the bounded manual loop from the current transcript. Returns
    ``(status, pending, draft_state)``. On an ``ask``-pause, persists the suspended state and
    returns ``status="awaiting_approval"`` with the pending actions (does **not** execute them)."""
    tools = tool_schemas()
    while True:
        if used >= MAX_TOOL_CALLS:  # step cap (§20): stop, leave what was gathered
            db.update_run(conn, run_id, status="done", step=used)
            return "done", [], draft_state

        step = provider.step(messages, tools)
        _record_llm_call(conn, run_id, ticket_id, "loop_step", provider, step.usage)
        messages.append(_assistant_turn(step))

        if step.stop_reason == "refusal" or not step.tool_calls:
            # Final assistant message (or a surfaced refusal) — no tool call → done (§6/§20).
            db.update_run(conn, run_id, status="done", step=used)
            return "done", [], draft_state

        used += len(step.tool_calls)
        turn_results: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []

        for tc in step.tool_calls:
            decision = gate.classify(tc.name)
            if decision.action == GateAction.EXECUTE:
                result, is_error = _run_tool(conn, run_id, ticket_id, used, tc.name, tc.args)
                if tc.name == "draft_reply" and not is_error:
                    # Faithfulness-check the draft in the orchestrator (not the tool) and surface
                    # the verdict back into the tool_result before it is fed to the model (R2).
                    draft_state = _check_draft_faithfulness(
                        conn, run_id, ticket_id, provider, tc.args, result
                    )
                turn_results.append(_tool_result_block(tc.id, result, is_error))
                if decision.cls == ToolClass.state_change:
                    # auto state-change: record the audit decision (even an errored attempt).
                    db.insert_action_log(
                        conn,
                        ticket_id=ticket_id,
                        run_id=run_id,
                        tool=tc.name,
                        decision="auto",
                        proposed_args_json=json.dumps(tc.args),
                        final_args_json=json.dumps(tc.args),
                        approver=None,
                        result_json=json.dumps(result),
                    )
            elif decision.action == GateAction.PAUSE:
                # The gate stops the write here — it is NOT executed (the trust story, §8).
                pending.append(
                    {"id": tc.id, "tool": tc.name, "args": tc.args, "rationale": step.text}
                )
            else:  # BLOCK (policy=deny)
                err = {"error": "blocked by policy", "tool": tc.name, "is_error": True}
                turn_results.append(_tool_result_block(tc.id, err, True))
                db.insert_action_log(
                    conn,
                    ticket_id=ticket_id,
                    run_id=run_id,
                    tool=tc.name,
                    decision="blocked",
                    proposed_args_json=json.dumps(tc.args),
                    final_args_json=None,
                    approver=None,
                    result_json=json.dumps(err),
                )

        if pending:
            # Suspend: persist the in-flight transcript + the results already computed this turn
            # (reads/blocks) + the pending calls. approve() reloads this exact state (R3, T9).
            state = {
                "messages": messages,
                "partial_results": turn_results,
                "pending": pending,
                "ticket_id": ticket_id,
                "tool_calls_used": used,
                "draft": draft_state,
                "policy": policy_map,
                "triage": triage.model_dump(mode="json"),
            }
            db.update_run(
                conn, run_id, status="awaiting_approval", step=used, messages_json=json.dumps(state)
            )
            return "awaiting_approval", pending, draft_state

        # Every tool_use block answered → send all results as ONE user turn and continue.
        messages.append({"role": "user", "content": turn_results})


# ---------------------------------------------------------------------------
# Outcome assembly (from the ledger, never the prose — §11/§20)
# ---------------------------------------------------------------------------


def _resolve_citations(conn: Any, chunk_ids: list[str]) -> list[Citation]:
    citations: list[Citation] = []
    for chunk_id in chunk_ids:
        row = conn.execute(
            "SELECT id, source, url FROM kb_chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if row is not None:
            citations.append(Citation(chunk_id=row["id"], source=row["source"], url=row["url"]))
    return citations


def _resolve_cited_chunks(conn: Any, chunk_ids: list[str]) -> list[dict[str, Any]]:
    """Resolve cited ``chunk_id`` s to ``{chunk_id, source, text}`` (the SOURCE the judge reads).

    Distinct from :func:`_resolve_citations` (which returns ``Citation`` objects for the Outcome,
    without the chunk *text*). Unknown ids are skipped.
    """
    chunks: list[dict[str, Any]] = []
    for chunk_id in chunk_ids:
        row = conn.execute(
            "SELECT id, source, text FROM kb_chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if row is not None:
            chunks.append({"chunk_id": row["id"], "source": row["source"], "text": row["text"]})
    return chunks


def _check_draft_faithfulness(
    conn: Any,
    run_id: str,
    ticket_id: str | None,
    provider: ProviderClient,
    args: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Run the §10 faithfulness check on a freshly-drafted reply (Split 04 R2).

    Resolves the cited chunks, runs :func:`faithfulness.check`, writes a priced
    ``llm_calls(kind="faithfulness")`` row (so it counts toward ``$/ticket``, §13), surfaces the
    verdict back to the model via the tool result (it ``may revise`` via a new ``draft_reply``,
    §20 — never blocked, never a gate input), and returns the ``draft_state`` for the Outcome.
    """
    body = args.get("body", "")
    citation_ids = list(args.get("citations", []))
    cited = _resolve_cited_chunks(conn, citation_ids)
    verdict, usage = faithfulness.check(body, cited, provider)
    _record_llm_call(conn, run_id, ticket_id, "faithfulness", provider, usage)
    verdict_json = verdict.model_dump(mode="json")
    result["faithfulness"] = verdict_json  # the model sees the verdict in the next turn
    return {"body": body, "citations": citation_ids, "faithfulness": verdict_json}


def _assemble_outcome(
    conn: Any,
    run_id: str,
    ticket_id: str | None,
    triage: Triage,
    status: str,
    pending: list[dict[str, Any]],
    draft_state: dict[str, Any] | None,
    provider_name: str,
    model_name: str,
    started_at: float,
) -> Outcome:
    cost = float(
        conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    )

    taken: list[ActionResult] = []
    for row in conn.execute(
        "SELECT tool, proposed_args_json, final_args_json, decision, approver, result_json "
        "FROM actions_log WHERE run_id = ? AND decision IN ('auto', 'approved') ORDER BY id",
        (run_id,),
    ).fetchall():
        args_json = row["final_args_json"] or row["proposed_args_json"]
        taken.append(
            ActionResult(
                tool=row["tool"],
                args=json.loads(args_json) if args_json else {},
                result=json.loads(row["result_json"]) if row["result_json"] else None,
                decision=Decision(row["decision"]),
                approver=row["approver"],
            )
        )

    actions_pending = [
        ApprovalRequest(
            id=p["id"], tool=p["tool"], args=p["args"], rationale=p.get("rationale", "")
        )
        for p in pending
    ]

    draft_reply = None
    if draft_state is not None:
        faith = draft_state.get("faithfulness")
        draft_reply = DraftReply(
            body=draft_state.get("body", ""),
            citations=_resolve_citations(conn, draft_state.get("citations", [])),
            faithfulness=Faithfulness.model_validate(faith) if faith is not None else None,
        )

    return Outcome(
        id=run_id,
        run_id=run_id,
        ticket_id=ticket_id,
        triage=triage,
        status=status,  # type: ignore[arg-type]
        actions_taken=taken,
        actions_pending=actions_pending,
        draft_reply=draft_reply,
        cost_usd=cost,
        latency_s=round(time.perf_counter() - started_at, 6),
        provider=provider_name,
        model=model_name,
        prompt_version=PROMPT_VERSION,
        n_runs=1,
    )


# ---------------------------------------------------------------------------
# Public surface: handle() / approve() / the invariant
# ---------------------------------------------------------------------------


def handle(
    ticket: str,
    *,
    provider: str = "anthropic",
    model: str | None = None,
    policy: str | dict[str, str] = "default",
    approve_all: bool = False,
    run_id: str | None = None,
    store_dir: str | None = None,
    _provider: ProviderClient | None = None,
) -> Outcome:
    """Triage a ticket and run the gated agent loop, returning an :class:`Outcome` (§5, §16).

    Auto-executes reads; ``ask`` state-changes **pause** at the gate (``status`` becomes
    ``awaiting_approval`` with ``actions_pending[]``) — call :func:`approve` to resume. With
    ``approve_all`` (or ``policy="auto"``) every state-change auto-executes (still audited
    ``decision="auto"``). The run persists to a per-run file DB keyed by ``run_id``.

    ``policy`` is a preset name (``auto``/``default``/``strict``) or a ``{tool: policy}`` dict
    of per-tool overrides applied on top of ``default`` (e.g. ``{"escalate": "deny"}``).
    """
    started = time.perf_counter()
    provider_obj = _provider if _provider is not None else make_provider(provider, model)
    rid = run_id or _new_run_id()
    path = _run_db_path(rid, store_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = db.reset_to_seed(path)
    try:
        db.insert_run(
            conn,
            id=rid,
            ticket_id=None,
            provider=provider_obj.provider,
            model=provider_obj.model,
            prompt_version=PROMPT_VERSION,
            status="running",
        )
        triage_obj, usage = provider_obj.structured_output(
            TRIAGE_SYSTEM, triage_user_content(ticket), Triage
        )
        assert isinstance(triage_obj, Triage)  # output_format=Triage guarantees the type
        _record_llm_call(conn, rid, None, "triage", provider_obj, usage)

        if approve_all:
            policy_map = build_policy("auto")
        elif isinstance(policy, dict):
            policy_map = build_policy("default", overrides=policy)
        else:
            policy_map = build_policy(policy)
        gate = Gate(policy_map)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": agent_first_user_content(ticket, triage_obj)}
        ]
        status, pending, draft_state = _drive(
            conn, rid, provider_obj, gate, messages, 0, None, None, policy_map, triage_obj
        )
        return _assemble_outcome(
            conn,
            rid,
            None,
            triage_obj,
            status,
            pending,
            draft_state,
            provider_obj.provider,
            provider_obj.model,
            started,
        )
    finally:
        conn.close()


def approve(
    outcome_id: str,
    decisions: list[dict[str, Any]],
    *,
    store_dir: str | None = None,
    approver: str = "operator",
    _provider: ProviderClient | None = None,
) -> Outcome:
    """Decide ALL pending actions of a suspended run and resume the loop (turn-granular, §8).

    ``decisions`` is ``[{"approval_id": ID, "decision": "allow"|"reject", "edited_args"?: {...}}]``
    — a batch over the suspended turn's pending actions. ``allow`` executes the write
    (``actions_log`` decision ``approved``); ``reject`` records ``rejected`` and feeds back an
    ``is_error`` tool_result. **Every** ``tool_use`` block in the turn gets a matching
    ``tool_result`` in one user turn before resuming (API requirement). Reopens the run by id
    from its file DB (``check_same_thread=False``), so this works across processes (R3, E6).
    """
    started = time.perf_counter()
    path = _run_db_path(outcome_id, store_dir)
    if not os.path.exists(path):
        raise ValueError(f"no persisted run found for outcome {outcome_id!r} (looked at {path})")
    conn = db.connect(path)  # reopen the durable file DB by id
    try:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (outcome_id,)).fetchone()
        if run is None:
            raise ValueError(f"unknown run {outcome_id!r}")
        if run["status"] != "awaiting_approval":
            raise ValueError(
                f"run {outcome_id!r} is not awaiting approval (status={run['status']!r})"
            )

        state = json.loads(run["messages_json"])
        messages: list[dict[str, Any]] = state["messages"]
        pending: list[dict[str, Any]] = state["pending"]
        ticket_id = state["ticket_id"]
        used = state["tool_calls_used"]
        draft_state = state["draft"]
        policy_map = state["policy"]
        triage = Triage.model_validate(state["triage"])

        decisions_by_id = {d["approval_id"]: d for d in decisions}
        pending_ids = [p["id"] for p in pending]
        missing = [pid for pid in pending_ids if pid not in decisions_by_id]
        if missing:
            raise ValueError(
                "turn-granular approval: a decision is required for every pending action; "
                f"missing {missing}"
            )
        unknown = [aid for aid in decisions_by_id if aid not in pending_ids]
        if unknown:
            raise ValueError(f"unknown approval id(s) {unknown}; pending are {pending_ids}")

        # Start from the read/block results already computed at suspend time; add the decisions.
        results_by_id = {b["tool_use_id"]: b for b in state["partial_results"]}
        for p in pending:
            d = decisions_by_id[p["id"]]
            verb = d["decision"]
            if verb == "allow":
                args = d.get("edited_args") or p["args"]
                result, is_error = _run_tool(conn, outcome_id, ticket_id, used, p["tool"], args)
                db.insert_action_log(
                    conn,
                    ticket_id=ticket_id,
                    run_id=outcome_id,
                    tool=p["tool"],
                    decision="approved",
                    proposed_args_json=json.dumps(p["args"]),
                    final_args_json=json.dumps(args),
                    approver=approver,
                    result_json=json.dumps(result),
                )
                results_by_id[p["id"]] = _tool_result_block(p["id"], result, is_error)
            elif verb == "reject":
                err = {"error": "rejected by operator", "tool": p["tool"], "is_error": True}
                db.insert_action_log(
                    conn,
                    ticket_id=ticket_id,
                    run_id=outcome_id,
                    tool=p["tool"],
                    decision="rejected",
                    proposed_args_json=json.dumps(p["args"]),
                    final_args_json=None,
                    approver=approver,
                    result_json=json.dumps(err),
                )
                results_by_id[p["id"]] = _tool_result_block(p["id"], err, True)
            else:
                raise ValueError(f"decision must be 'allow' or 'reject', got {verb!r}")

        # Send EVERY tool_use block's result in ONE user turn, ordered to match the turn (§8).
        assistant_turn = messages[-1]
        ordered: list[dict[str, Any]] = []
        for block in assistant_turn["content"]:
            if block.get("type") == "tool_use":
                rb = results_by_id.get(block["id"])
                if rb is None:  # pragma: no cover - defensive; every block is decided above
                    raise RuntimeError(f"missing tool_result for tool_use {block['id']!r}")
                ordered.append(rb)
        messages.append({"role": "user", "content": ordered})

        provider_obj = (
            _provider if _provider is not None else make_provider(run["provider"], run["model"])
        )
        gate = Gate(policy_map)
        status, new_pending, draft_state = _drive(
            conn,
            outcome_id,
            provider_obj,
            gate,
            messages,
            used,
            ticket_id,
            draft_state,
            policy_map,
            triage,
        )
        return _assemble_outcome(
            conn,
            outcome_id,
            ticket_id,
            triage,
            status,
            new_pending,
            draft_state,
            run["provider"],
            run["model"],
            started,
        )
    finally:
        conn.close()


def assert_no_unapproved_writes(run_id: str, conn: Any) -> None:
    """The never-acts-without-approval invariant (§8/§14) — CI hard gate (T7).

    Raises ``AssertionError`` unless **every** ``state_change`` execution row in ``tool_calls``
    is covered by a matching ``actions_log`` decision in ``{auto, approved}``. Conversely a
    ``rejected``/``blocked`` action leaves no ``tool_calls`` execution row, so it never appears
    in ``executed`` and the check holds for it implicitly.
    """
    executed = Counter(
        r["tool"]
        for r in conn.execute("SELECT tool FROM tool_calls WHERE run_id = ?", (run_id,)).fetchall()
        if r["tool"] in STATE_CHANGE_TOOLS
    )
    authorized = Counter(
        r["tool"]
        for r in conn.execute(
            "SELECT tool FROM actions_log WHERE run_id = ? AND decision IN ('auto', 'approved')",
            (run_id,),
        ).fetchall()
    )
    for tool, n in executed.items():
        if authorized[tool] < n:
            raise AssertionError(
                f"never-acts-without-approval VIOLATED in run {run_id!r}: {tool} executed {n} "
                f"time(s) but has only {authorized[tool]} auto/approved decision(s)"
            )
