"""StubProvider plays scripted results with zero network (T5, R4)."""

from __future__ import annotations

import pytest

from relay.models import Triage
from relay.provider import StubProvider
from relay.provider.base import ModelStep, NormalizedToolCall, Usage


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


def test_importable_from_installed_package() -> None:
    # R4: must live in the package (eval/ and api/ import it across layers), not in tests/.
    from relay import StubProvider as Top
    from relay.provider.stub import StubProvider as Deep

    assert Top is Deep is StubProvider


def test_stub_triage_returns_scripted_result() -> None:
    stub = StubProvider(triage_result=_triage())
    out = stub.triage("I was charged twice")
    assert out.intent.value == "billing_dispute"
    assert out.extracted_fields.order_ref == "A-4471"
    # The call was recorded.
    assert stub.calls[0][0] == "structured_output"


def test_stub_structured_results_queue_takes_precedence() -> None:
    first, second = _triage(), _triage()
    stub = StubProvider(structured_results=[first, second])
    a, ua = stub.structured_output("sys", "user", Triage)
    b, ub = stub.structured_output("sys", "user", Triage)
    assert a is first and b is second
    assert ua.input_tokens == ub.input_tokens == 10  # default stub usage


def test_stub_structured_without_script_raises() -> None:
    with pytest.raises(LookupError):
        StubProvider().structured_output("sys", "user", Triage)


def test_stub_steps_are_played_in_order_then_default_end() -> None:
    # "turn 1 proposes lookup_customer, turn 2 proposes update_ticket, turn 3 ends" (R4).
    steps = [
        ModelStep(
            text="looking up",
            tool_calls=[
                NormalizedToolCall(id="tu_1", name="lookup_customer", args={"email": "a@b"})
            ],
            usage=Usage(input_tokens=100, output_tokens=10),
            stop_reason="tool_use",
        ),
        ModelStep(
            text="proposing the write",
            tool_calls=[
                NormalizedToolCall(
                    id="tu_2",
                    name="update_ticket",
                    args={"ticket_id": "T-1042", "status": "pending_refund"},
                )
            ],
            usage=Usage(input_tokens=120, output_tokens=14),
            stop_reason="tool_use",
        ),
    ]
    stub = StubProvider(steps=steps)
    s1 = stub.step([], [])
    s2 = stub.step([], [])
    s3 = stub.step([], [])  # exhausted -> default end-of-turn

    assert s1.tool_calls[0].name == "lookup_customer"
    assert s2.tool_calls[0].name == "update_ticket"
    # args are dicts (parsed), never raw strings.
    assert isinstance(s2.tool_calls[0].args, dict)
    assert s3.stop_reason == "end_turn" and s3.tool_calls == []


def test_stub_usage_accumulates_across_steps() -> None:
    steps = [
        ModelStep(usage=Usage(input_tokens=100, output_tokens=10), stop_reason="tool_use"),
        ModelStep(usage=Usage(input_tokens=120, output_tokens=14), stop_reason="end_turn"),
    ]
    stub = StubProvider(steps=steps)
    played = [stub.step([], []), stub.step([], [])]
    total = sum((s.usage for s in played), Usage())
    assert total.input_tokens == 220
    assert total.output_tokens == 24


def test_stub_satisfies_provider_protocol() -> None:
    from relay.provider.base import ProviderClient

    assert isinstance(StubProvider(triage_result=_triage()), ProviderClient)
