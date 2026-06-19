"""Tier-2 (@api): real end-to-end on OpenAI / Azure gpt-5.5. Auto-skipped without a key.

T7 / E1 / E5 — distributional: assert the *gate behavior* (a state-change paused, the invariant
holds, $/ticket > 0 priced from OpenAI pricing), never exact model wording or tool sequence
(LLM output is not reproducible, §12). Mirrors the Anthropic ``test_agent_api.py`` so the two
providers are exercised identically.
"""

from __future__ import annotations

import os

import pytest

from relay import approve, handle
from relay.agent import assert_no_unapproved_writes
from relay.backend import db

pytestmark = pytest.mark.api

_HAS_KEY = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))

_BILLING = (
    "Hi — I was charged twice for my Pro subscription this month (order #A-4471). "
    "Please refund the duplicate charge. — jane@acme.com"
)


@pytest.mark.skipif(not _HAS_KEY, reason="needs OPENAI_API_KEY or AZURE_OPENAI_API_KEY")
def test_real_billing_dispute_pauses_a_write_then_approves_on_openai(tmp_path) -> None:
    sd = str(tmp_path)
    out = handle(_BILLING, provider="openai", policy="default", store_dir=sd)

    # Distributional: the exact tools vary, but the run must be a valid terminal/suspended state,
    # cost a real number priced from OpenAI pricing, and never violate the invariant.
    assert out.status in ("awaiting_approval", "done")
    assert out.provider == "openai" and out.model == "gpt-5.5"
    assert out.cost_usd > 0.0  # $/ticket sourced from llm_calls, OpenAI pricing

    conn = db.connect(f"{sd}/runs/{out.id}.db")
    try:
        assert_no_unapproved_writes(out.id, conn)
    finally:
        conn.close()

    if out.status == "awaiting_approval":
        assert out.actions_pending  # at least one state-change paused at the gate
        decisions = [{"approval_id": p.id, "decision": "allow"} for p in out.actions_pending]
        out2 = approve(out.id, decisions, store_dir=sd)
        assert out2.status in ("awaiting_approval", "done")
        conn = db.connect(f"{sd}/runs/{out.id}.db")
        try:
            assert_no_unapproved_writes(out.id, conn)
        finally:
            conn.close()
