"""Relay — a gated, eval-proven, multi-provider agentic ops-automation engine.

Public surface grows split by split. Split 02 added the **provider seam** (``ProviderClient``,
``ModelStep``, ``Usage``, ``StubProvider``) and the ``triage`` convenience. Split 03 added the
loop + gate: ``handle`` / ``approve`` and the ``assert_no_unapproved_writes`` invariant. Split 04
adds the **faithfulness check** on drafted replies (``draft_reply.faithfulness`` is now populated,
logged as an ``llm_calls`` row of kind ``faithfulness`` and counted in ``$/ticket``) and finishes
the CLI. The Anthropic SDK is imported lazily (only when a real Anthropic provider is
constructed), so ``import relay`` stays light.

Library usage (spec §16)::

    import relay

    out = relay.handle("I was charged twice (order A-4471) — please refund. — jane@acme.com")
    if out.status == "awaiting_approval":               # a state-change paused at the gate
        decisions = [{"approval_id": p.id, "decision": "allow"} for p in out.actions_pending]
        out = relay.approve(out.id, decisions)          # out.id == run_id; decides all pending
    print(out.draft_reply.faithfulness.all_grounded, out.cost_usd)
"""

from __future__ import annotations

from .agent import approve, assert_no_unapproved_writes, handle, make_provider
from .gate import Gate
from .models import Faithfulness, Outcome, Triage
from .provider import (
    MissingAPIKeyError,
    ModelStep,
    NormalizedToolCall,
    ProviderClient,
    StubProvider,
    Usage,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "triage",
    "handle",
    "approve",
    "assert_no_unapproved_writes",
    "Gate",
    "Outcome",
    "Triage",
    "Faithfulness",
    "ProviderClient",
    "ModelStep",
    "NormalizedToolCall",
    "Usage",
    "StubProvider",
    "MissingAPIKeyError",
]


def triage(ticket: str, provider: str = "anthropic", model: str | None = None) -> Triage:
    """Classify one ticket into a :class:`Triage` (intent, priority, fields, confidence).

    Thin convenience over a provider's ``triage()`` (spec §16). Constructs the provider and
    delegates; raises a clear error if the provider's API key is missing.
    """
    return make_provider(provider, model).triage(ticket)
