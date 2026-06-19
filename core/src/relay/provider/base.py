"""The provider seam (spec §9): one interface, one normalized output shape.

Everything downstream of ``provider/`` — the agent loop, the gate, the backend, the eval
harness — is **provider-agnostic**. Only the concrete provider modules
(``anthropic.py``, and ``openai.py`` in Split 05) know a wire format; they normalize
into the types defined here so swapping ``--provider`` changes one constructor and nothing
else (§9).

``Usage`` is the **same** token-bucket model the cost module prices and the ``llm_calls``
ledger stores (re-exported from :mod:`relay.cost` so there is one source of truth); it gains a
``+`` here-by-import so a run can accumulate triage + each loop step + faithfulness.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..cost import Usage  # re-exported: the canonical, cost-aware token-bucket model.
from ..models import Triage

__all__ = [
    "Usage",
    "NormalizedToolCall",
    "ModelStep",
    "ProviderClient",
    "ProviderError",
    "MissingAPIKeyError",
]


class ProviderError(RuntimeError):
    """Base class for catchable provider-layer failures (missing key, bad response)."""


class MissingAPIKeyError(ProviderError):
    """Raised when a provider is constructed without an API key — surfaced, never a crash."""


class NormalizedToolCall(BaseModel):
    """One proposed tool call, normalized across providers.

    ``args`` is **already parsed** (``json.loads``-equivalent) into a dict — never the raw
    JSON string (conformance rule, §API). The gate (Split 03) classifies by ``name`` and the
    tool executes with ``args``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    args: dict[str, Any] = {}


class ModelStep(BaseModel):
    """The normalized result of exactly **one** tool-use turn (§9).

    ``text`` is the assistant prose around the tool call(s) — Split 03 uses it as
    ``ApprovalRequest.rationale`` (§11), so it must be captured, not discarded.
    ``stop_reason`` is normalized across providers (``tool_use`` / ``end_turn`` / ``refusal`` /
    ``max_tokens`` / …). ``usage`` is normalized token buckets for pricing (Split 03 prices +
    persists; ``step`` itself writes no DB rows).
    """

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    tool_calls: list[NormalizedToolCall] = []
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str = ""
    stop_details: dict[str, Any] | None = None


@runtime_checkable
class ProviderClient(Protocol):
    """One interface, two backends (§9). Carries ``provider`` + ``model`` for cost keying."""

    provider: str
    model: str

    def structured_output(
        self, system: str, user: str, schema_model: type[BaseModel]
    ) -> tuple[BaseModel, Usage]:
        """Generic single structured-output call: parsed model validated against
        ``schema_model`` **plus** normalized ``Usage`` (so the caller can price it).

        This is the one place a provider's structured-output wire format lives.
        ``triage`` wraps it; Split 04's faithfulness judge reuses it so it works on
        OpenAI for free at Split 05 (R1)."""
        ...

    def triage(self, ticket: str) -> Triage:
        """Thin convenience over ``structured_output(TRIAGE_SYSTEM, ticket, Triage)``.

        Returns just ``Triage``; Split 03 calls ``structured_output`` directly when it also
        needs the ``Usage`` to write the ``llm_calls`` row."""
        ...

    def step(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelStep:
        """Exactly **one** tool-use turn. Proposes tool calls; does **not** execute them and
        does **not** auto-loop (that is the manual loop's job, §6) — so the gate can intercept
        every state-changing call before it fires."""
        ...
