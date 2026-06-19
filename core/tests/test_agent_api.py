"""Tier-2 (@api): real end-to-end on Anthropic. Auto-skipped without ANTHROPIC_API_KEY.

T11 — distributional: assert the *gate behavior* (a state-change paused, the invariant holds,
$/ticket > 0), never exact model wording or tool sequence (LLM output is not reproducible, §12).
"""

from __future__ import annotations

import os

import pytest

from relay import approve, handle
from relay.agent import assert_no_unapproved_writes
from relay.backend import db

pytestmark = pytest.mark.api

_BILLING = (
    "Hi — I was charged twice for my Pro subscription this month (order #A-4471). "
    "Please refund the duplicate charge. — jane@acme.com"
)


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_t11_real_billing_dispute_pauses_a_write_then_approves(tmp_path) -> None:
    sd = str(tmp_path)
    out = handle(_BILLING, provider="anthropic", policy="default", store_dir=sd)

    # Distributional: the exact tools vary, but a state-change must have paused at the gate.
    assert out.status in ("awaiting_approval", "done")
    assert out.cost_usd > 0.0  # $/ticket sourced from llm_calls

    conn = db.connect(f"{sd}/runs/{out.id}.db")
    try:
        assert_no_unapproved_writes(out.id, conn)
    finally:
        conn.close()

    if out.status == "awaiting_approval":
        assert out.actions_pending  # at least one write paused
        decisions = [{"approval_id": p.id, "decision": "allow"} for p in out.actions_pending]
        out2 = approve(out.id, decisions, store_dir=sd)
        assert out2.status in ("awaiting_approval", "done")
        conn = db.connect(f"{sd}/runs/{out.id}.db")
        try:
            assert_no_unapproved_writes(out.id, conn)
        finally:
            conn.close()
