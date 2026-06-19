"""Relay — a gated, eval-proven, multi-provider agentic ops-automation engine.

Public surface grows split by split. Split 02 adds the **provider seam** (``ProviderClient``,
``ModelStep``, ``Usage``, ``StubProvider``) and the ``triage`` convenience. ``handle`` /
``approve`` (the loop + gate) arrive in Split 03. The Anthropic SDK is imported lazily (only
when a real Anthropic provider is constructed), so ``import relay`` stays light.
"""

from __future__ import annotations

from .models import Triage
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
    return _make_provider(provider, model).triage(ticket)


def _make_provider(provider: str, model: str | None) -> ProviderClient:
    """Construct a provider by name. OpenAI arrives in Split 05."""
    if provider == "anthropic":
        from .provider.anthropic import AnthropicProvider

        return AnthropicProvider(model=model) if model else AnthropicProvider()
    if provider == "openai":
        raise NotImplementedError("the OpenAI provider is built in Split 05")
    raise ValueError(f"unknown provider {provider!r}")
