"""Provider seam value types: Usage merge, NormalizedToolCall, ModelStep (R1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay.cost import Usage
from relay.provider.base import ModelStep, NormalizedToolCall


def test_usage_is_the_cost_module_usage() -> None:
    # One source of truth: the seam re-exports cost.Usage (not a parallel copy).
    from relay.provider import base

    assert base.Usage is Usage


def test_usage_add_is_bucketwise() -> None:
    a = Usage(input_tokens=10, output_tokens=2, cache_read_tokens=5, cache_creation_tokens=1)
    b = Usage(input_tokens=3, output_tokens=4, cache_read_tokens=0, cache_creation_tokens=7)
    total = a + b
    assert total.input_tokens == 13
    assert total.output_tokens == 6
    assert total.cache_read_tokens == 5
    assert total.cache_creation_tokens == 8
    # Operands are not mutated.
    assert a.input_tokens == 10 and b.input_tokens == 3


def test_usage_sum_accumulates_over_a_run() -> None:
    # $/ticket sums triage + each loop step + faithfulness — sum() must work (starts at int 0).
    calls = [Usage(input_tokens=i, output_tokens=1) for i in range(1, 5)]
    total = sum(calls)
    assert total.input_tokens == 10
    assert total.output_tokens == 4
    # sum() with an explicit start also works.
    assert sum(calls, Usage()).input_tokens == 10


def test_usage_add_rejects_non_usage() -> None:
    with pytest.raises(TypeError):
        _ = Usage(input_tokens=1) + 5  # type: ignore[operator]


def test_normalized_tool_call_args_default_and_forbid_extra() -> None:
    call = NormalizedToolCall(id="tu_1", name="update_ticket", args={"ticket_id": "T-1"})
    assert call.args == {"ticket_id": "T-1"}
    assert NormalizedToolCall(id="x", name="y").args == {}
    with pytest.raises(ValidationError):
        NormalizedToolCall(id="x", name="y", bogus=1)  # type: ignore[call-arg]


def test_model_step_defaults() -> None:
    step = ModelStep()
    assert step.text == ""
    assert step.tool_calls == []
    assert step.usage == Usage()
    assert step.stop_reason == ""
    assert step.stop_details is None


def test_model_step_holds_calls_and_usage() -> None:
    step = ModelStep(
        text="proposing",
        tool_calls=[NormalizedToolCall(id="tu_1", name="lookup_customer", args={"email": "a@b"})],
        usage=Usage(input_tokens=100, output_tokens=20),
        stop_reason="tool_use",
    )
    assert step.tool_calls[0].name == "lookup_customer"
    assert isinstance(step.tool_calls[0].args, dict)
    assert step.usage.input_tokens == 100
