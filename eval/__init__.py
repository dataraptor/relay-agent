"""Relay eval harness (Split 06) — the headline artifact.

Imports the installed ``relay`` engine directly (no server) and drives the full
triage → loop → gate → faithfulness stack over a gold scenario set, producing a
**leaderboard** with two registers:

- **Deterministic (CI gate, 100%, no key):** never-acts-without-approval, gate-policy
  correctness, schema validity — proven on the frozen ``must_gate/`` subset with the
  ``StubProvider`` (free, no network).
- **Distributional (mean ± spread over N≥3, both providers):** routing, field-extraction,
  action-correctness, reply-faithfulness, plus ``$/ticket`` and latency per provider.

This layer depends only on ``core`` (it never duplicates loop or gate logic) — see
``00-conventions.md`` §1. Run it with ``python -m eval.run`` (see :mod:`eval.run`).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
