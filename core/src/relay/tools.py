"""The seven-tool action surface (spec §7, Appendix A).

Each tool declares its **class in code** (``read`` / ``read_class`` / ``state_change``) — the
model never sets it; the gate (Split 03) reads :data:`TOOL_CLASS`. ``execute()`` only *performs*
the action when called; it does **not** decide whether it is allowed to run (that is the gate's
job). Keeping approval out of here is the clean seam the safety story rests on (§4/§6).

Tool input schemas are emitted **non-strict** (``additionalProperties: false`` + a ``required``
list of only the genuinely-required args). This is the deliberate answer to the strict-mode
optional-arg footgun (§Notes): ``update_ticket(status?, fields?, note?)`` must be able to set
only ``status`` and ``fields`` is a free-form object that strict mode can't express. The *triage*
call keeps native strict structured outputs; the *loop tools* use non-strict tool calling. The
same shape is used for both providers so Split 05 (OpenAI) drops in clean.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from rank_bm25 import BM25Okapi

from .backend import db
from .models import (
    DraftReplyInput,
    DraftReplyOutput,
    EscalateInput,
    EscalateOutput,
    KbHit,
    LookupCustomerInput,
    LookupCustomerOutput,
    RouteTicketInput,
    RouteTicketOutput,
    SearchKbInput,
    SearchKbOutput,
    SendReplyInput,
    SendReplyOutput,
    ToolClass,
    UpdateTicketInput,
    UpdateTicketOutput,
)

__all__ = ["Tool", "ToolError", "REGISTRY", "TOOL_CLASS", "tool_schemas", "execute"]


class ToolError(Exception):
    """A tool failed to validate input or execute.

    ``to_result()`` is the structured payload the loop (Split 03) feeds back as a ``tool_result``
    with ``is_error: true`` so the model can retry (§7). Defined here so the error *shape* is
    owned by the tool layer, not invented in the loop.
    """

    def __init__(self, tool: str, message: str) -> None:
        super().__init__(f"{tool}: {message}")
        self.tool = tool
        self.message = message

    def to_result(self) -> dict[str, Any]:
        return {"error": self.message, "tool": self.tool, "is_error": True}


# --- provider-agnostic tool-schema construction ------------------------------


def _strip_titles(node: Any) -> None:
    """Remove cosmetic ``title`` keys recursively (keeps the schema tight; not load-bearing)."""
    if isinstance(node, dict):
        node.pop("title", None)
        for value in node.values():
            _strip_titles(value)
    elif isinstance(node, list):
        for item in node:
            _strip_titles(item)


def _input_schema(model: type[BaseModel]) -> dict[str, Any]:
    """A non-strict provider tool-input schema: ``additionalProperties:false`` + ``required``.

    ``required`` is exactly Pydantic's required set (fields without a default) — so optional args
    are genuinely optional, no strict-mode all-keys-required footgun. Free-form objects (e.g.
    ``update_ticket.fields``) keep their own ``additionalProperties`` and are untouched.
    """
    schema = model.model_json_schema()
    _strip_titles(schema)
    schema["type"] = "object"
    schema["additionalProperties"] = False
    schema.setdefault("properties", {})
    schema.setdefault("required", [])
    return schema


# --- the Tool record ---------------------------------------------------------

Executor = Callable[[BaseModel, sqlite3.Connection], BaseModel]


@dataclass(frozen=True)
class Tool:
    """One tool: a declared class (code constant), I/O models, schema, and an executor."""

    name: str
    cls: ToolClass
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    executor: Executor

    def schema(self) -> dict[str, Any]:
        """The provider-agnostic tool-schema dict for ``tools=[...]`` (Anthropic-native shape)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": _input_schema(self.input_model),
        }

    def execute(self, args: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
        """Validate ``args``, perform the action against the backend, return a validated dict.

        Raises :class:`ToolError` (carrying a feed-back-able payload) on invalid input or a
        backend failure. **Performs only — never decides approval.**
        """
        try:
            parsed = self.input_model.model_validate(args)
        except ValidationError as exc:
            raise ToolError(self.name, f"invalid input: {exc.errors(include_url=False)}") from exc
        try:
            out = self.executor(parsed, conn)
        except (KeyError, ValueError, sqlite3.Error) as exc:
            raise ToolError(self.name, str(exc)) from exc
        return out.model_dump()


# --- executors (perform only; gating is Split 03) ----------------------------


def _exec_lookup_customer(
    inp: LookupCustomerInput, conn: sqlite3.Connection
) -> LookupCustomerOutput:
    if inp.email is None and inp.customer_id is None:
        raise ValueError("lookup_customer requires email or customer_id")
    row = db.get_customer(conn, email=inp.email, customer_id=inp.customer_id)
    if row is None:
        raise KeyError(f"customer not found (email={inp.email!r}, id={inp.customer_id!r})")
    flags = json.loads(row.get("flags_json") or "{}")
    recent = db.get_tickets_for_customer(conn, row["id"])
    customer = {k: row[k] for k in ("id", "email", "name", "plan", "status", "mrr") if k in row}
    return LookupCustomerOutput(
        customer=customer,
        plan=row["plan"],
        status=row["status"],
        recent_tickets=recent,
        flags=flags,
    )


# Module-level token cache keyed by chunk text is unnecessary: the corpus is tiny (~14 rows)
# and rebuilt per call, which keeps search_kb deterministic and stateless.
def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _exec_search_kb(inp: SearchKbInput, conn: sqlite3.Connection) -> SearchKbOutput:
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id, source, url, section, text FROM kb_chunks ORDER BY id"
        ).fetchall()
    ]
    if not rows:
        return SearchKbOutput(results=[])
    corpus = [_tokenize(f"{r['section']} {r['text']}") for r in rows]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_tokenize(inp.query))
    # Deterministic: sort by descending score, ties broken by chunk id.
    ranked = sorted(zip(scores, rows, strict=True), key=lambda pair: (-pair[0], pair[1]["id"]))
    k = max(0, inp.k)
    hits = [
        KbHit(
            chunk_id=row["id"],
            text=row["text"],
            source=row["source"],
            url=row["url"],
            score=round(float(score), 6),
        )
        for score, row in ranked[:k]
    ]
    return SearchKbOutput(results=hits)


def _exec_draft_reply(inp: DraftReplyInput, conn: sqlite3.Connection) -> DraftReplyOutput:
    # Drafting composes text and writes nothing to the backend. The faithfulness slot is wired
    # but left None here — Split 04 fills it (the check runs in the orchestrator, not the tool).
    return DraftReplyOutput(ok=True, faithfulness=None)


def _exec_send_reply(inp: SendReplyInput, conn: sqlite3.Connection) -> SendReplyOutput:
    # Mock "send": a deterministic message id. No outbox table exists; the actions_log audit row
    # is the gate's responsibility (Split 03), not the tool's.
    digest = hashlib.sha1(f"{inp.to}\n{inp.body}".encode()).hexdigest()[:12]
    return SendReplyOutput(message_id=f"msg-{digest}")


def _exec_update_ticket(inp: UpdateTicketInput, conn: sqlite3.Connection) -> UpdateTicketOutput:
    ticket = db.update_ticket(
        conn, inp.ticket_id, status=inp.status, fields=inp.fields, note=inp.note
    )
    return UpdateTicketOutput(ticket=ticket)


def _exec_route_ticket(inp: RouteTicketInput, conn: sqlite3.Connection) -> RouteTicketOutput:
    ticket = db.route_ticket(conn, inp.ticket_id, inp.queue)
    return RouteTicketOutput(ticket=ticket)


def _exec_escalate(inp: EscalateInput, conn: sqlite3.Connection) -> EscalateOutput:
    ticket = db.escalate(conn, inp.ticket_id, level=inp.level, rationale=inp.rationale)
    return EscalateOutput(ticket=ticket)


# --- registry + class map (consumed by the gate in Split 03) -----------------

_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="lookup_customer",
        cls=ToolClass.read,
        description=(
            "Look up a customer by email or customer_id. Returns their plan, status, recent "
            "tickets, and account flags. Use freely to gather context."
        ),
        input_model=LookupCustomerInput,
        output_model=LookupCustomerOutput,
        executor=_exec_lookup_customer,
    ),
    Tool(
        name="search_kb",
        cls=ToolClass.read,
        description=(
            "Search the policy knowledge base (BM25) and return the top-k matching chunks with "
            "their chunk_id, source, and url. Cite a returned chunk_id for any factual claim."
        ),
        input_model=SearchKbInput,
        output_model=SearchKbOutput,
        executor=_exec_search_kb,
    ),
    Tool(
        name="draft_reply",
        cls=ToolClass.read_class,
        description=(
            "Compose a draft reply to the customer (no message is sent). Provide the body and the "
            "chunk_ids it cites. Returns a faithfulness verdict slot; sending is a separate tool."
        ),
        input_model=DraftReplyInput,
        output_model=DraftReplyOutput,
        executor=_exec_draft_reply,
    ),
    Tool(
        name="send_reply",
        cls=ToolClass.state_change,
        description=(
            "Send a reply email to the customer. Irreversible — gated for human approval. "
            "Provide the recipient, body, and cited chunk_ids."
        ),
        input_model=SendReplyInput,
        output_model=SendReplyOutput,
        executor=_exec_send_reply,
    ),
    Tool(
        name="update_ticket",
        cls=ToolClass.state_change,
        description=(
            "Update a ticket's status and/or whitelisted fields (status, queue, priority, intent, "
            "subject). Gated for human approval. Set only the fields you intend to change."
        ),
        input_model=UpdateTicketInput,
        output_model=UpdateTicketOutput,
        executor=_exec_update_ticket,
    ),
    Tool(
        name="route_ticket",
        cls=ToolClass.state_change,
        description=(
            "Route a ticket to a queue (billing/tech/abuse/…). A write; policy may auto-approve it."
        ),
        input_model=RouteTicketInput,
        output_model=RouteTicketOutput,
        executor=_exec_route_ticket,
    ),
    Tool(
        name="escalate",
        cls=ToolClass.state_change,
        description=(
            "Escalate a ticket to a human/urgent level with a rationale. A write; policy may "
            "auto-approve so escalation is never blocked."
        ),
        input_model=EscalateInput,
        output_model=EscalateOutput,
        executor=_exec_escalate,
    ),
)

#: name -> Tool. The single registry the gate and loop consume.
REGISTRY: dict[str, Tool] = {tool.name: tool for tool in _TOOLS}

#: name -> ToolClass. A **code constant** — the model cannot change a tool's class (§7/§8).
TOOL_CLASS: dict[str, ToolClass] = {tool.name: tool.cls for tool in _TOOLS}


def tool_schemas() -> list[dict[str, Any]]:
    """All provider-agnostic tool schemas, in registry order, for ``step(..., tools=...)``."""
    return [tool.schema() for tool in _TOOLS]


def execute(name: str, args: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    """Look up and execute a tool by name. Raises :class:`ToolError` for an unknown tool."""
    tool = REGISTRY.get(name)
    if tool is None:
        raise ToolError(name, "unknown tool")
    return tool.execute(args, conn)
