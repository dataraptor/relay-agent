"""Relay — a gated, eval-proven, multi-provider agentic ops-automation engine.

Public surface grows split by split. Split 02 added the **provider seam** (``ProviderClient``,
``ModelStep``, ``Usage``, ``StubProvider``) and the ``triage`` convenience. Split 03 adds the
loop + gate: ``handle`` / ``approve`` and the ``assert_no_unapproved_writes`` invariant. The
Anthropic SDK is imported lazily (only when a real Anthropic provider is constructed), so
``import relay`` stays light.
"""

from __future__ import annotations

from .agent import approve, assert_no_unapproved_writes, handle, make_provider
from .gate import Gate
from .models import Outcome, Triage
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
