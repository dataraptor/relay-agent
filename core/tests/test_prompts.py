"""T4 — prompts present, stable, and free of cache-busting ticket interpolation."""

from __future__ import annotations

from relay import prompts as P
from relay.models import ExtractedFields, Triage

_SYSTEM_PROMPTS = (P.TRIAGE_SYSTEM, P.AGENT_SYSTEM, P.FAITHFULNESS_SYSTEM)


def test_prompt_version_is_non_empty_string() -> None:
    assert isinstance(P.PROMPT_VERSION, str) and P.PROMPT_VERSION.strip()


def test_system_prompts_non_empty() -> None:
    assert all(isinstance(p, str) and p.strip() for p in _SYSTEM_PROMPTS)


def test_load_bearing_phrases_present() -> None:
    assert "do not assume it executed" in P.AGENT_SYSTEM
    assert "Never claim an action succeeded" in P.AGENT_SYSTEM
    assert "SUPPORTED / CONTRADICTED / NOT_ENOUGH_INFO" in P.FAITHFULNESS_SYSTEM
    assert "Classify only" in P.TRIAGE_SYSTEM


def test_no_system_prompt_interpolates_ticket_text() -> None:
    # Cache rule (§13): ticket/date/run-id must never be baked into a *system* prompt.
    for p in _SYSTEM_PROMPTS:
        assert "{ticket" not in p
        assert "{ticket}" not in p
        assert "%s" not in p
        # No actual interpolation happened (only literal braces describing enum sets remain).
        assert "<ticket" not in p.lower()


def test_triage_user_content_format() -> None:
    assert P.triage_user_content("hello") == "TICKET:\nhello"


def test_agent_first_user_content_includes_ticket_and_triage_summary() -> None:
    triage = Triage(
        intent="billing_dispute",
        priority="high",
        extracted_fields=ExtractedFields(customer_email="jane@acme.com", order_ref="A-4471"),
        confidence="high",
    )
    content = P.agent_first_user_content("charged twice", triage)
    assert content.startswith("TICKET:\ncharged twice")
    assert "intent=billing_dispute" in content
    assert "priority=high" in content
    assert "customer_email=jane@acme.com" in content
    # The system prompt itself is passed separately, not embedded in the user turn.
    assert P.AGENT_SYSTEM not in content
