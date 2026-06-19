"""Public library surface: relay.triage convenience + provider selection (R5)."""

from __future__ import annotations

import pytest

import relay
from relay.models import Triage
from relay.provider import MissingAPIKeyError, StubProvider


def _triage() -> Triage:
    return Triage.model_validate(
        {
            "intent": "billing_dispute",
            "priority": "high",
            "confidence": "high",
            "extracted_fields": {
                "customer_email": "jane@acme.com",
                "order_ref": "A-4471",
                "amount": None,
                "product": None,
            },
        }
    )


def test_triage_convenience_delegates_to_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubProvider(triage_result=_triage())
    monkeypatch.setattr(relay, "make_provider", lambda provider, model: stub)
    out = relay.triage("I was charged twice (order A-4471)")
    assert isinstance(out, Triage)
    assert out.intent.value == "billing_dispute"
    assert out.extracted_fields.order_ref == "A-4471"


def test_triage_without_key_raises_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        relay.triage("anything")


def test_make_provider_anthropic_with_explicit_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercises the `if model` branch; construction still fails without a key (expected).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        relay.make_provider("anthropic", "claude-opus-4-8")


def test_make_provider_openai_is_deferred_to_split_05() -> None:
    with pytest.raises(NotImplementedError):
        relay.make_provider("openai", None)


def test_make_provider_unknown_raises() -> None:
    with pytest.raises(ValueError):
        relay.make_provider("nope", None)
