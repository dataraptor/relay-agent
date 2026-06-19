"""Tier-2 (@api): the real §10 faithfulness judge on Anthropic. Auto-skipped without a key.

Distributional, never exact (LLM output is not reproducible, §12): a reply that only states
facts present in the cited chunk should land ``all_grounded=True`` by majority over N runs (T8);
a reply that invents a policy absent from the chunk should produce at least one non-SUPPORTED
label / ``all_grounded=False`` (T9).
"""

from __future__ import annotations

import os

import pytest

from relay.agent import make_provider
from relay.faithfulness import check

pytestmark = pytest.mark.api

_CHUNK = {
    "chunk_id": "kb-refund-001",
    "source": "refund-policy",
    "text": "Duplicate charges are refunded in full within 5-7 business days once verified.",
}

_N = 3


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_t8_real_grounded_reply_majority_grounded() -> None:
    provider = make_provider("anthropic", None)
    reply = (
        "Once verified, your duplicate charge will be refunded in full within 5-7 business days."
    )
    grounded = 0
    for _ in range(_N):
        verdict, usage = check(reply, [_CHUNK], provider)
        grounded += int(verdict.all_grounded)
        assert usage.input_tokens > 0  # a real inference happened
    assert grounded >= 2  # majority of N grounded


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_t9_real_ungrounded_reply_is_flagged() -> None:
    provider = make_provider("anthropic", None)
    # Invents a 30-day timeline and a "store credit" policy absent from the SOURCE.
    reply = (
        "We'll issue your refund as store credit, and it can take up to 30 days to appear. "
        "We also waive your next month's fee as an apology."
    )
    flagged = 0
    for _ in range(_N):
        verdict, _ = check(reply, [_CHUNK], provider)
        if not verdict.all_grounded or any(c.label != "SUPPORTED" for c in verdict.claims):
            flagged += 1
    assert flagged >= 2  # fabrication caught in the majority of N runs
