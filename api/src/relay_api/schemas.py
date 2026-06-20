"""Request/response contracts for the Relay HTTP API (Split 07 R1).

**One serialization, not two.** The engine's ``Outcome`` (spec §11, the ``--json`` CLI contract)
is the source of truth for shared field names; this module *reuses* the engine's ``Triage`` /
``ApprovalRequest`` / ``ActionResult`` / ``DraftReply`` models verbatim and only **adds**
presentation-derived fields the UI timeline binds to (``trace``, ``records``, ``cost.by_call``).
RunView never *renames* a §11 field — it is a superset of ``Outcome``, projected from the run's
ledger (``tool_calls`` + ``actions_log`` + ``llm_calls``), never from the model's prose (§20).

This contract is **frozen for Split 08** — the existing frontend binds to RunView exactly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from relay.models import (
    ActionResult,
    ApprovalRequest,
    DraftReply,
    Faithfulness,
    Triage,
)

Provider = Literal["anthropic", "openai"]
Policy = Literal["auto", "default", "strict"]
RunStatus = Literal["done", "awaiting_approval", "error"]

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class HandleRequest(BaseModel):
    """Body of ``POST /handle`` (R3). Mirrors the CLI ``handle`` surface (§16)."""

    model_config = ConfigDict(extra="forbid")

    ticket: str = Field(min_length=1, description="The raw support-ticket text.")
    provider: Provider = "anthropic"
    model: str | None = None
    policy: Policy = "default"


class DecisionItem(BaseModel):
    """One operator decision over a pending action (turn-granular, §8)."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str
    decision: Literal["allow", "reject"]
    edited_args: dict[str, Any] | None = None


class ApproveRequest(BaseModel):
    """Body of ``POST /approve`` (R3). ``decisions`` must cover **every** pending action of the
    suspended turn (a single-pending convenience is just a one-element array)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    decisions: list[DecisionItem] = Field(min_length=1)


# ---------------------------------------------------------------------------
# RunView sub-models (presentation enrichments derived from the ledger)
# ---------------------------------------------------------------------------


class CitationView(BaseModel):
    """A resolved citation enriched with the cited chunk's ``text`` (the UI shows the snippet).

    The engine's ``Citation`` (§11) carries only ``{chunk_id, source, url}``; the UI's citation
    popover also needs the chunk text, resolved here from ``kb_chunks``.
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    text: str
    source: str
    url: str


class DraftView(BaseModel):
    """The drafted-reply sub-block carried on the ``draft_reply`` trace step (R1)."""

    model_config = ConfigDict(extra="forbid")

    body: str
    citations: list[CitationView] = []
    faithfulness: Faithfulness | None = None


class TraceStep(BaseModel):
    """One ordered step in the run timeline, reconstructed from the ledger (R1).

    ``state`` is the lifecycle of this step; ``decision`` is the gate's audit verdict (only set
    for state-change tools). A ``draft_reply`` step additionally carries ``draft``.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int
    tool: str
    cls: Literal["read", "read_class", "state_change"]
    args: dict[str, Any] = {}
    result_summary: str = ""
    latency_ms: int | None = None
    decision: Literal["auto", "approved", "rejected", "blocked"] | None = None
    state: Literal["executed", "awaiting", "rejected", "blocked"]
    rationale: str = ""
    draft: DraftView | None = None


class CustomerRecord(BaseModel):
    """The looked-up customer row the records panel binds to (UI §5.3)."""

    model_config = ConfigDict(extra="forbid")

    email: str | None = None
    plan: str | None = None
    status: str | None = None
    mrr: float | None = None
    flags: dict[str, Any] = {}


class TicketRecord(BaseModel):
    """The target ticket's current backend state (reflects committed writes)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: str | None = None
    queue: str | None = None


class ProposedChange(BaseModel):
    """A pending write's preview, e.g. ``status: open -> pending_refund`` (UI §5.3 'proposed').

    Field names are ``current``/``proposed`` (not ``from``/``to``) — ``from`` is a Python keyword
    and an alias round-trip would muddy the one-serialization rule. The diff is a presentation
    preview derived from the pending action's args + the ticket's current row, never a write.
    """

    model_config = ConfigDict(extra="forbid")

    field: str
    current: str | None = None
    proposed: str | None = None


class Records(BaseModel):
    """The backend rows a run touched (R1). Drives the records panel's
    empty -> populated -> proposed -> committed states."""

    model_config = ConfigDict(extra="forbid")

    customer: CustomerRecord | None = None
    ticket: TicketRecord | None = None
    proposed: ProposedChange | None = None


class CostCall(BaseModel):
    """One priced inference from ``llm_calls`` (kind = triage|loop_step|faithfulness, §13)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    cost_usd: float


class Tokens(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0


class CostBreakdown(BaseModel):
    """The auditable cost block (R1). ``total_usd == sum(by_call.cost_usd) == Outcome.cost_usd``."""

    model_config = ConfigDict(extra="forbid")

    total_usd: float = 0.0
    by_call: list[CostCall] = []
    tokens: Tokens = Tokens()
    latency_s: float = 0.0


# ---------------------------------------------------------------------------
# RunView — the response of /handle and /approve (a superset of Outcome, §11)
# ---------------------------------------------------------------------------


class RunView(BaseModel):
    """The full view-model the frontend binds to (R1). A superset of the engine's ``Outcome``:
    same §11 field names (``id``, ``triage``, ``status``, ``actions_pending``, ``actions_taken``,
    ``provider``, ``model``, ``prompt_version``, ``n_runs``) plus the presentation enrichments
    (``trace``, ``records``, ``cost``) the UI timeline/records/cost panels need."""

    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    ticket_id: str | None = None
    triage: Triage
    status: RunStatus
    trace: list[TraceStep] = []
    actions_pending: list[ApprovalRequest] = []
    actions_taken: list[ActionResult] = []
    draft_reply: DraftReply | None = None
    records: Records | None = None
    cost: CostBreakdown = CostBreakdown()
    provider: str
    model: str
    prompt_version: str
    n_runs: int = 1


# ---------------------------------------------------------------------------
# Meta endpoints
# ---------------------------------------------------------------------------


class ExampleTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    ticket: str
    lock: bool = False


class ConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[str]
    models_by_provider: dict[str, list[str]]
    policies: list[str]
    default_provider: str
    default_model_by_provider: dict[str, str]
    #: True when the server runs the offline/CI StubProvider path (no live model). Honest label
    #: so the UI and a reviewer can tell a canned demo run from a billed live run (Split 10).
    stub: bool = False


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    providers_available: dict[str, bool]
    #: See ``ConfigResponse.stub`` — surfaced on health too so a probe sees the run mode.
    stub: bool = False


# ---------------------------------------------------------------------------
# Error envelope (R4) — a single structured shape; never a 500 stack trace
# ---------------------------------------------------------------------------


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    message: str
    provider: str | None = None
    env_var: str | None = None
    retriable: bool | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
