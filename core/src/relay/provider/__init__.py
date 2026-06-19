"""The provider seam: the normalized interface (``base``) and its backends.

``AnthropicProvider`` is imported lazily by callers (it pulls in the ``anthropic`` SDK), so
importing this package stays light. ``StubProvider`` is pure-Python and safe to import anywhere.
"""

from __future__ import annotations

from .base import (
    MissingAPIKeyError,
    ModelStep,
    NormalizedToolCall,
    ProviderClient,
    ProviderError,
    Usage,
)
from .stub import StubProvider

__all__ = [
    "Usage",
    "ModelStep",
    "NormalizedToolCall",
    "ProviderClient",
    "ProviderError",
    "MissingAPIKeyError",
    "StubProvider",
]
