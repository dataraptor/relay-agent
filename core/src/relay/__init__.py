"""Relay — a gated, eval-proven, multi-provider agentic ops-automation engine.

The public library surface (``handle``, ``approve``, ``triage``) is added in the splits
that build those pieces (02–04). Split 01 only guarantees the package imports cleanly and
exposes ``__version__``; importing not-yet-built modules here would break that guarantee.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
