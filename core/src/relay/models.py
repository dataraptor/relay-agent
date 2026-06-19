"""Canonical data contracts for Relay (spec §11 + Appendix A).

**Field names and enum members are a cross-stack contract.** The UI (`app/Relay.dc.html`),
the eval scenarios (`eval/scenarios/*.yaml`), and the API layer all bind to these exact names
(`extracted_fields.customer_email`, `order_ref`, `amount`, `product`, `intent`, `priority`,
`confidence`, `decision ∈ {auto,approved,rejected,blocked}`). Renaming anything here cascades
into every later split — change only with the spec.

These are *schemas only* in Split 01: tool execution (Split 02), the agent loop / gate
(Split 03), and the faithfulness check (Split 04) populate and consume them later.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

# ---------------------------------------------------------------------------
# Enums (Appendix A — members must match the spec verbatim)
# ---------------------------------------------------------------------------


class Intent(StrEnum):
    billing_dispute = "billing_dispute"
    refund_request = "refund_request"
    technical_issue = "technical_issue"
    account_access = "account_access"
    feature_request = "feature_request"
    abuse_report = "abuse_report"
    general_question = "general_question"
    spam = "spam"


class Priority(StrEnum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class Confidence(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class ToolClass(StrEnum):
    """A tool's gate class, declared in code (§7/§8) — the model never sets it.

    The spec writes the middle class as "read-class"; code uses ``read_class``.
    ``TOOL_CLASS_LABELS`` maps to the hyphenated display string when one is needed.
    """

    read = "read"
    read_class = "read_class"
    state_change = "state_change"


#: Hyphenated display labels for the (otherwise underscore) tool classes.
TOOL_CLASS_LABELS: dict[ToolClass, str] = {
    ToolClass.read: "read",
    ToolClass.read_class: "read-class",
    ToolClass.state_change: "state_change",
}


class Decision(StrEnum):
    """Values recorded in ``actions_log.decision`` (§8/§11)."""

    auto = "auto"
    approved = "approved"
    rejected = "rejected"
    blocked = "blocked"


# ---------------------------------------------------------------------------
# Structured-output contracts (sent to the model)
# ---------------------------------------------------------------------------


class ExtractedFields(BaseModel):
    """Typed fields pulled from the ticket (§11, Appendix A).

    All four fields are **nullable with a Python default of ``None``** for ergonomic
    construction. For provider structured outputs they must be expressed as *required keys
    that may be null* (not omitted) — OpenAI strict mode (Split 05) has no "optional" field.
    Use :func:`strict_json_schema` to emit the provider-ready shape (every key in ``required``,
    each typed as a null-union, ``additionalProperties: false``). The model returns ``null``
    for an absent field (matching the §5 worked example's ``amount: null``).
    """

    model_config = ConfigDict(extra="forbid")

    customer_email: str | None = None
    order_ref: str | None = None
    amount: float | None = None
    product: str | None = None


class Triage(BaseModel):
    """The structured triage object (§5/§11) — a provider structured-output target.

    Its JSON schema sets ``additionalProperties: false`` and avoids unsupported keywords
    (``minLength``/``maximum``/recursion) so it compiles on both Anthropic and OpenAI.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    priority: Priority
    extracted_fields: ExtractedFields
    confidence: Confidence


# ---------------------------------------------------------------------------
# Faithfulness verdict (§10/§14) — schema here; the check runs in Split 04.
# Defined before the tool I/O models because draft_reply's output embeds it.
# ---------------------------------------------------------------------------

FaithLabel = Literal["SUPPORTED", "CONTRADICTED", "NOT_ENOUGH_INFO"]


class ClaimVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    label: FaithLabel


class Faithfulness(BaseModel):
    """Per-claim grounding verdict for a drafted reply (§10).

    ``all_grounded`` is true iff every claim is ``SUPPORTED``.
    """

    model_config = ConfigDict(extra="forbid")

    all_grounded: bool
    claims: list[ClaimVerdict] = []


# ---------------------------------------------------------------------------
# Tool I/O schemas — one input + one output model per tool (§7).
# Schemas only here; execution is wired in Split 02.
# ---------------------------------------------------------------------------


class LookupCustomerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = None
    customer_id: str | None = None


class LookupCustomerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer: dict[str, Any]
    plan: str
    status: str
    recent_tickets: list[dict[str, Any]] = []
    flags: dict[str, Any] = {}


class KbHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    text: str
    source: str
    url: str
    score: float


class SearchKbInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    k: int = 4


class SearchKbOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[KbHit] = []


class Citation(BaseModel):
    """A resolved reply citation — plain ``{source, url, chunk_id}`` (§API conformance:
    Anthropic structured outputs are incompatible with the native citations feature)."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source: str
    url: str


class DraftReplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str
    citations: list[str] = []  # chunk_ids the reply cites; resolved to Citation objects


class DraftReplyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    faithfulness: Faithfulness | None = None


class SendReplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str
    body: str
    citations: list[str] = []


class SendReplyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str


class UpdateTicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    status: str | None = None
    fields: dict[str, Any] | None = None
    note: str | None = None


class UpdateTicketOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket: dict[str, Any]


class RouteTicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    queue: str


class RouteTicketOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket: dict[str, Any]


class EscalateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    level: str
    rationale: str


class EscalateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket: dict[str, Any]


# ---------------------------------------------------------------------------
# Assembled types (§11) — built in code by the orchestrator (Split 03).
# ---------------------------------------------------------------------------


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    args: dict[str, Any] = {}
    rationale: str = ""
    cls: ToolClass


class ApprovalRequest(BaseModel):
    """A state-change call paused at the gate.

    ``rationale`` is the assistant text that preceded this ``tool_use`` block in the turn
    (captured by the orchestrator — NOT a model-supplied arg). ``escalate``'s own ``rationale``
    arg, when present, is appended downstream (§11).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    tool: str
    args: dict[str, Any] = {}
    rationale: str = ""


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    args: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    decision: Decision
    approver: str | None = None
    latency_ms: int | None = None


class DraftReply(BaseModel):
    """The assembled drafted reply on an ``Outcome`` (§11)."""

    model_config = ConfigDict(extra="forbid")

    body: str
    citations: list[Citation] = []
    faithfulness: Faithfulness | None = None


class Outcome(BaseModel):
    """The result of a ``handle()`` run (§11).

    ``id == run_id`` — ``approve()`` keys on it. Construct with just ``id`` and ``run_id``
    defaults to it; passing a mismatched ``run_id`` is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str | None = None
    ticket_id: str | None = None
    triage: Triage
    status: Literal["done", "awaiting_approval", "error"]
    actions_taken: list[ActionResult] = []
    actions_pending: list[ApprovalRequest] = []
    draft_reply: DraftReply | None = None
    cost_usd: float = 0.0
    latency_s: float = 0.0
    provider: str
    model: str
    prompt_version: str
    n_runs: int = 1

    @model_validator(mode="after")
    def _sync_run_id(self) -> Outcome:
        if self.run_id is None:
            self.run_id = self.id
        elif self.run_id != self.id:
            raise ValueError(f"run_id ({self.run_id!r}) must equal id ({self.id!r})")
        return self


# ---------------------------------------------------------------------------
# Provider-ready strict JSON schema (used by Split 05's OpenAI seam and the
# Anthropic manual structured-output path).
# ---------------------------------------------------------------------------


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return ``model``'s JSON schema in provider strict-mode shape.

    Recursively, for every object node: every property is forced into ``required`` and
    ``additionalProperties`` is set to ``false``; ``default`` keys are stripped (OpenAI
    strict mode rejects them). Nullable fields stay as null-unions — i.e. *required keys that
    may be null*, never omitted. Compiles unchanged on both Anthropic and OpenAI strict mode.
    """

    schema = model.model_json_schema()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(schema)
    return schema
