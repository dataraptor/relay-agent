"""The run store + the RunView projection (Split 07 R2, R1).

**The run store** (the single hardest correctness property of this split). Core's ``handle()``
opens a per-run isolated DB and, on ``ask``, persists the in-flight transcript to
``runs.messages_json``. Over HTTP, ``/handle`` and ``/approve`` are two separate requests, so the
run must live in a **durable per-run file DB** both requests reach. The store gives the engine a
single base directory (``<base>/runs/<run_id>.db`` — the exact scheme ``relay`` already uses) and
keeps an in-process **registry** mapping ``run_id -> RunRecord`` (the live provider object used for
the run + metadata). ``/handle`` creates + runs; ``/approve`` looks up the record and resumes the
*same* loop. Different runs are isolated (each has its own DB); a lost/expired ``run_id`` is a
clean 404, never a crash. Cleanup is a best-effort size cap (demo scope, §2).

**The RunView projection** is pure *serialization* of the run's ledger — never a re-decision.
``project_run_view`` reads ``tool_calls`` + ``actions_log`` + ``llm_calls`` (and the ``Outcome``)
and assembles the ordered trace, the touched records, and the cost breakdown the UI binds to.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from relay.backend import db
from relay.models import Outcome, ToolClass
from relay.provider.base import ProviderClient
from relay.tools import TOOL_CLASS

from .schemas import (
    CitationView,
    CostBreakdown,
    CostCall,
    CustomerRecord,
    DraftView,
    ProposedChange,
    Records,
    RunView,
    TicketRecord,
    Tokens,
    TraceStep,
)

#: Best-effort cap on retained runs (their file DBs). Oldest is evicted past this (demo scope).
DEFAULT_MAX_RUNS = 256


@dataclass
class RunRecord:
    """One run's liveness entry: the provider used (``None`` for a real, re-constructable
    provider; a ``StubProvider`` instance in tests) plus the request metadata."""

    run_id: str
    provider_obj: ProviderClient | None
    provider: str
    model: str | None
    extra: dict[str, Any] = field(default_factory=dict)


class RunNotFoundError(LookupError):
    """A ``run_id`` is unknown/expired in the registry (→ a 404 envelope, never a crash)."""


class RunStore:
    """Durable per-run store + in-process registry (R2).

    The engine writes each run's DB under ``<base_dir>/runs/<run_id>.db``; this object only tracks
    liveness + the provider object so ``/approve`` can resume the exact suspended loop. Thread-safe
    registry mutation (uvicorn serves sync routes in a worker pool; T4 hits it concurrently).
    """

    def __init__(self, base_dir: str | None = None, *, max_runs: int = DEFAULT_MAX_RUNS) -> None:
        self.base_dir = (
            base_dir
            or os.environ.get("RELAY_API_STORE_DIR")
            or os.path.join(tempfile.gettempdir(), "relay-api-runs")
        )
        os.makedirs(os.path.join(self.base_dir, "runs"), exist_ok=True)
        self._max_runs = max_runs
        self._registry: OrderedDict[str, RunRecord] = OrderedDict()
        self._lock = threading.Lock()

    # -- ids + paths ----------------------------------------------------------

    def new_run_id(self) -> str:
        return f"run_{uuid.uuid4().hex[:16]}"

    def db_path(self, run_id: str) -> str:
        """Mirror ``relay.agent._run_db_path`` so the engine and the store agree on the file."""
        return os.path.join(self.base_dir, "runs", f"{run_id}.db")

    def open_db(self, run_id: str) -> Any:
        """Reopen a run's durable DB read-side for the projection (``check_same_thread=False``)."""
        return db.connect(self.db_path(run_id))

    # -- registry -------------------------------------------------------------

    def register(
        self, run_id: str, provider_obj: ProviderClient | None, provider: str, model: str | None
    ) -> None:
        with self._lock:
            self._registry[run_id] = RunRecord(run_id, provider_obj, provider, model)
            self._registry.move_to_end(run_id)
            self._evict_if_needed()

    def get(self, run_id: str) -> RunRecord:
        with self._lock:
            record = self._registry.get(run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            self._registry.move_to_end(run_id)
            return record

    def _evict_if_needed(self) -> None:
        """Best-effort size cap: drop the oldest run(s) and their file DBs (demo cleanup, R2)."""
        while len(self._registry) > self._max_runs:
            old_id, _ = self._registry.popitem(last=False)
            path = self.db_path(old_id)
            try:  # pragma: no cover - filesystem cleanup is best-effort
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# RunView projection (serialization of the ledger — never a re-decision)
# ---------------------------------------------------------------------------


def _summarize(tool: str, result: dict[str, Any] | None) -> str:
    """A short, human-readable one-line summary of a tool's success result (for the trace)."""
    if not isinstance(result, dict):
        return ""
    if tool == "lookup_customer":
        parts = [str(result.get("plan") or "?"), str(result.get("status") or "?")]
        flags = result.get("flags") or {}
        on = [k for k, v in flags.items() if v]
        if on:
            parts.append("flags: " + ", ".join(on))
        return " · ".join(parts)
    if tool == "search_kb":
        return f"{len(result.get('results') or [])} chunks"
    if tool == "draft_reply":
        return "draft composed"
    if tool == "send_reply":
        return f"sent ({result.get('message_id', 'msg')})"
    if tool in ("update_ticket", "route_ticket", "escalate"):
        ticket = result.get("ticket") or {}
        bits = [str(ticket.get("id") or "")]
        if ticket.get("status"):
            bits.append(f"status={ticket['status']}")
        if ticket.get("queue"):
            bits.append(f"queue={ticket['queue']}")
        return " · ".join(b for b in bits if b)
    return ""


def _resolve_chunk_text(conn: Any, chunk_id: str) -> str:
    row = conn.execute("SELECT text FROM kb_chunks WHERE id = ?", (chunk_id,)).fetchone()
    return row["text"] if row is not None else ""


def _draft_view(conn: Any, outcome: Outcome) -> DraftView | None:
    draft = outcome.draft_reply
    if draft is None:
        return None
    citations = [
        CitationView(
            chunk_id=c.chunk_id,
            text=_resolve_chunk_text(conn, c.chunk_id),
            source=c.source,
            url=c.url,
        )
        for c in draft.citations
    ]
    return DraftView(body=draft.body, citations=citations, faithfulness=draft.faithfulness)


def _build_trace(conn: Any, outcome: Outcome) -> list[TraceStep]:
    """Reconstruct the ordered timeline from the ledger (R1).

    Order: executed ``tool_calls`` (true execution order, by row id) → non-executed decisions
    (``rejected``/``blocked`` from ``actions_log``) → still-pending actions (``awaiting``).
    Auto/approved state-change executions are matched to their ``actions_log`` decision in lockstep
    (both ledgers are written in the same per-iteration order), so each carries its audit verdict.
    """
    run_id = outcome.run_id
    tool_calls = conn.execute(
        "SELECT tool, args_json, result_json, latency_ms FROM tool_calls "
        "WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    action_logs = conn.execute(
        "SELECT tool, proposed_args_json, decision FROM actions_log WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    executed_decisions = [
        a["decision"] for a in action_logs if a["decision"] in ("auto", "approved")
    ]
    non_executed = [a for a in action_logs if a["decision"] in ("rejected", "blocked")]

    draft = _draft_view(conn, outcome)
    steps: list[TraceStep] = []
    sc_idx = 0
    for tc in tool_calls:
        tool = tc["tool"]
        cls = TOOL_CLASS.get(tool, ToolClass.state_change)
        args = json.loads(tc["args_json"]) if tc["args_json"] else {}
        result = json.loads(tc["result_json"]) if tc["result_json"] else None
        decision = None
        if cls == ToolClass.state_change:
            if sc_idx < len(executed_decisions):
                decision = executed_decisions[sc_idx]
            sc_idx += 1
        steps.append(
            TraceStep(
                seq=len(steps),
                tool=tool,
                cls=cls.value,  # type: ignore[arg-type]
                args=args,
                result_summary=_summarize(tool, result),
                latency_ms=tc["latency_ms"],
                decision=decision,  # type: ignore[arg-type]
                state="executed",
                draft=draft if tool == "draft_reply" else None,
            )
        )

    for a in non_executed:
        args = json.loads(a["proposed_args_json"]) if a["proposed_args_json"] else {}
        verb = a["decision"]
        steps.append(
            TraceStep(
                seq=len(steps),
                tool=a["tool"],
                cls="state_change",
                args=args,
                result_summary="rejected — no change"
                if verb == "rejected"
                else "blocked by policy",
                decision=verb,  # type: ignore[arg-type]
                state=verb,  # type: ignore[arg-type]
            )
        )

    for p in outcome.actions_pending:
        steps.append(
            TraceStep(
                seq=len(steps),
                tool=p.tool,
                cls="state_change",
                args=p.args,
                result_summary="awaiting approval",
                state="awaiting",
                rationale=p.rationale,
            )
        )
    return steps


def _latest_lookup_result(conn: Any, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT result_json FROM tool_calls WHERE run_id = ? AND tool = 'lookup_customer' "
        "ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is None or row["result_json"] is None:
        return None
    return json.loads(row["result_json"])


def _find_ticket_id(outcome: Outcome, lookup: dict[str, Any] | None) -> str | None:
    """The ticket the run is about: any action's ``ticket_id``, else the customer's latest one."""
    for action in outcome.actions_pending:
        if action.args.get("ticket_id"):
            return str(action.args["ticket_id"])
    for taken in outcome.actions_taken:
        if taken.args.get("ticket_id"):
            return str(taken.args["ticket_id"])
    if lookup:
        recent = lookup.get("recent_tickets") or []
        if recent and isinstance(recent[0], dict) and recent[0].get("id"):
            return str(recent[0]["id"])
    return None


def _proposed_change(
    outcome: Outcome, ticket_id: str, ticket_row: dict[str, Any]
) -> ProposedChange | None:
    """A presentation preview of the pending write against the ticket's current row (UI §5.3)."""
    for action in outcome.actions_pending:
        if str(action.args.get("ticket_id")) != ticket_id:
            continue
        new_status = action.args.get("status")
        if new_status and new_status != ticket_row.get("status"):
            return ProposedChange(
                field="status", current=ticket_row.get("status"), proposed=new_status
            )
        new_queue = action.args.get("queue")
        if new_queue and new_queue != ticket_row.get("queue"):
            return ProposedChange(
                field="queue", current=ticket_row.get("queue"), proposed=new_queue
            )
    return None


def _build_records(conn: Any, outcome: Outcome) -> Records | None:
    """The touched backend rows: looked-up customer + target ticket (+ proposed diff) (R1)."""
    run_id = outcome.run_id
    lookup = _latest_lookup_result(conn, run_id)

    customer = None
    if lookup:
        c = lookup.get("customer") or {}
        customer = CustomerRecord(
            email=c.get("email"),
            plan=lookup.get("plan"),
            status=lookup.get("status"),
            mrr=c.get("mrr"),
            flags=lookup.get("flags") or {},
        )

    ticket = None
    proposed = None
    ticket_id = _find_ticket_id(outcome, lookup)
    if ticket_id:
        row = db.get_ticket(conn, ticket_id)
        if row is not None:
            ticket = TicketRecord(id=row["id"], status=row.get("status"), queue=row.get("queue"))
            proposed = _proposed_change(outcome, ticket_id, row)

    if customer is None and ticket is None:
        return None
    return Records(customer=customer, ticket=ticket, proposed=proposed)


def _build_cost(conn: Any, outcome: Outcome) -> CostBreakdown:
    """The auditable cost block from ``llm_calls`` (§13). ``total_usd`` == ``Outcome.cost_usd``."""
    rows = conn.execute(
        "SELECT kind, cost_usd, input_tokens, output_tokens, cache_read_tokens, "
        "cache_creation_tokens FROM llm_calls WHERE run_id = ? ORDER BY id",
        (outcome.run_id,),
    ).fetchall()
    by_call = [CostCall(kind=r["kind"], cost_usd=r["cost_usd"]) for r in rows]
    tokens = Tokens(
        input=sum(r["input_tokens"] or 0 for r in rows),
        output=sum(r["output_tokens"] or 0 for r in rows),
        cache_read=sum(r["cache_read_tokens"] or 0 for r in rows),
        cache_creation=sum(r["cache_creation_tokens"] or 0 for r in rows),
    )
    return CostBreakdown(
        total_usd=outcome.cost_usd,
        by_call=by_call,
        tokens=tokens,
        latency_s=outcome.latency_s,
    )


def project_run_view(outcome: Outcome, conn: Any) -> RunView:
    """Project an engine ``Outcome`` + its persisted ledger into the frontend's ``RunView`` (R1).

    Pure serialization: every field traces back to the ledger or the ``Outcome``; nothing here
    decides approval or re-runs the model. ``run_id`` is guaranteed non-null by ``Outcome``.
    """
    assert outcome.run_id is not None  # Outcome syncs run_id == id
    return RunView(
        id=outcome.id,
        run_id=outcome.run_id,
        ticket_id=outcome.ticket_id,
        triage=outcome.triage,
        status=outcome.status,
        trace=_build_trace(conn, outcome),
        actions_pending=outcome.actions_pending,
        actions_taken=outcome.actions_taken,
        draft_reply=outcome.draft_reply,
        records=_build_records(conn, outcome),
        cost=_build_cost(conn, outcome),
        provider=outcome.provider,
        model=outcome.model,
        prompt_version=outcome.prompt_version,
        n_runs=outcome.n_runs,
    )
