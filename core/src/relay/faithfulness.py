"""The §10 reply-grounding check — one structured LLM call, provider-agnostic (Split 04).

Given a drafted reply and the KB chunks it cites, ask the model to label each factual claim
``SUPPORTED`` / ``CONTRADICTED`` / ``NOT_ENOUGH_INFO`` against the SOURCE, and return a single
``all_grounded`` boolean. ``all_grounded`` is **recomputed in code** from the per-claim labels
(true iff every claim is ``SUPPORTED``, §10) — so the boolean is a code invariant, not the
model's word, and a model that returns an inconsistent flag is corrected.

**Provider-agnostic by construction.** It rides on :meth:`ProviderClient.structured_output`
(the Split 02 seam), so it works on Anthropic now and on OpenAI for free at Split 05 — it never
touches a provider SDK directly.

**A check, not a gate.** The verdict measures reply *quality* (reported distributionally in
eval, Split 06). It is **never** a gate input — the safety gate (``gate.py``) is about
state-changes only. An ungrounded reply does not block the (separately-gated) write, and a
beautifully grounded reply still doesn't auto-send (§8 vs §14).
"""

from __future__ import annotations

from typing import Any

from .models import Faithfulness
from .prompts import FAITHFULNESS_SYSTEM
from .provider.base import ProviderClient, Usage

__all__ = ["check", "build_source", "FaithfulnessResult"]

#: The spec's R1 name for the result model. It **is** :class:`relay.models.Faithfulness` — the
#: same shape §11's ``draft_reply.faithfulness`` slot binds to — kept as one source of truth
#: rather than a duplicate type. Exposed here so callers can reference the spec's name.
FaithfulnessResult = Faithfulness


def build_source(reply_body: str, cited_chunks: list[dict[str, Any]]) -> str:
    """Build the judge's user turn: the cited chunks as ``SOURCE``, then the draft reply (§10).

    Each chunk dict carries at least ``chunk_id``/``source``/``text``. A reply that cites
    nothing still gets judged (the model will mark unsupported claims ``NOT_ENOUGH_INFO``).
    """
    if cited_chunks:
        lines = []
        for chunk in cited_chunks:
            cid = chunk.get("chunk_id", "?")
            src = chunk.get("source", "")
            text = chunk.get("text", "")
            label = f"[{cid}]" + (f" ({src})" if src else "")
            lines.append(f"{label} {text}".rstrip())
        source = "\n".join(lines)
    else:
        source = "(no sources were cited)"
    return f"SOURCE:\n{source}\n\nDRAFT REPLY:\n{reply_body}"


def check(
    reply_body: str,
    cited_chunks: list[dict[str, Any]],
    provider: ProviderClient,
) -> tuple[Faithfulness, Usage]:
    """Run the single §10 grounding check via ``provider.structured_output``.

    Returns ``(verdict, usage)`` — the validated :class:`Faithfulness` plus its normalized
    ``Usage`` so the caller can price the ``llm_calls(kind="faithfulness")`` row (R2). It's an
    LLM judge, so the verdict is reported distributionally in eval; here it is the single-call
    result with ``all_grounded`` derived from the labels (vacuously true for an empty claim set).
    """
    user = build_source(reply_body, cited_chunks)
    result, usage = provider.structured_output(FAITHFULNESS_SYSTEM, user, Faithfulness)
    assert isinstance(result, Faithfulness)  # output_format=Faithfulness guarantees the type
    grounded = all(claim.label == "SUPPORTED" for claim in result.claims)
    verdict = Faithfulness(all_grounded=grounded, claims=result.claims)
    return verdict, usage
