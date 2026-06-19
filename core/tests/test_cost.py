"""T1 — cost math: pinned pricing, the 3-bucket cache-aware formula, honest failure modes."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from relay.cost import PRICING, Usage, compute_cost, price


@pytest.mark.parametrize(
    ("model_id", "in_rate", "out_rate"),
    [
        ("claude-opus-4-8", 5.0, 25.0),
        ("claude-sonnet-4-6", 3.0, 15.0),
        ("claude-haiku-4-5", 1.0, 5.0),
    ],
)
def test_pinned_anthropic_pricing(model_id: str, in_rate: float, out_rate: float) -> None:
    assert price("anthropic", model_id) == (in_rate, out_rate)
    assert PRICING[("anthropic", model_id)] == (in_rate, out_rate)


@pytest.mark.parametrize(
    ("model_id", "in_rate", "out_rate"),
    [
        ("claude-opus-4-8", 5.0, 25.0),
        ("claude-sonnet-4-6", 3.0, 15.0),
        ("claude-haiku-4-5", 1.0, 5.0),
    ],
)
def test_simple_cost_matches_hand_calc(model_id: str, in_rate: float, out_rate: float) -> None:
    # No cache buckets: usd = (in*in_rate + out*out_rate) / 1e6.
    usage = Usage(input_tokens=10_000, output_tokens=2_000)
    expected = (10_000 * in_rate + 2_000 * out_rate) / 1_000_000
    assert math.isclose(compute_cost("anthropic", model_id, usage), expected, rel_tol=1e-9)


def test_cache_buckets_apply_multipliers() -> None:
    # Opus: in=5, out=25. Prove the 1.25x (write) and 0.10x (read) multipliers.
    usage = Usage(
        input_tokens=1_000,
        output_tokens=500,
        cache_creation_tokens=4_000,
        cache_read_tokens=2_000,
    )
    expected = (1_000 * 5.0 + 4_000 * 5.0 * 1.25 + 2_000 * 5.0 * 0.10 + 500 * 25.0) / 1_000_000
    got = compute_cost("anthropic", "claude-opus-4-8", usage)
    assert math.isclose(got, expected, rel_tol=1e-12)
    assert math.isclose(expected, 0.0435, rel_tol=1e-9)


def test_cache_read_is_cheaper_than_fresh_input() -> None:
    fresh = compute_cost("anthropic", "claude-sonnet-4-6", Usage(input_tokens=10_000))
    cached = compute_cost("anthropic", "claude-sonnet-4-6", Usage(cache_read_tokens=10_000))
    assert cached < fresh
    assert math.isclose(cached, fresh * 0.10, rel_tol=1e-9)


def test_zero_usage_is_zero_cost() -> None:
    assert compute_cost("anthropic", "claude-haiku-4-5", Usage()) == 0.0


def test_unknown_model_raises_keyerror_not_zero() -> None:
    with pytest.raises(KeyError):
        price("anthropic", "claude-does-not-exist")
    with pytest.raises(KeyError):
        compute_cost("anthropic", "claude-does-not-exist", Usage(input_tokens=1))


def test_openai_pricing_is_pinned_gpt_5_5() -> None:
    # Split 05: OpenAI gpt-5.5 pricing verified 2026-06-20 — input $5.00 / output $30.00 per MTok.
    assert price("openai", "gpt-5.5") == (5.0, 30.0)
    assert PRICING[("openai", "gpt-5.5")] == (5.0, 30.0)


def test_openai_cost_matches_hand_calc() -> None:
    # T3: OpenAI usage prices the full prompt as input; cache buckets are 0 (Open decision A),
    # so the cache multipliers never apply — usd = (in*5 + out*30) / 1e6.
    usage = Usage(input_tokens=10_000, output_tokens=2_000)
    expected = (10_000 * 5.0 + 2_000 * 30.0) / 1_000_000
    assert math.isclose(compute_cost("openai", "gpt-5.5", usage), expected, rel_tol=1e-12)


def test_unknown_openai_model_raises_keyerror_not_fabricated() -> None:
    # An unknown OpenAI model must raise (never a silent 0 / fabricated number).
    with pytest.raises(KeyError):
        price("openai", "gpt-does-not-exist")
    with pytest.raises(KeyError):
        compute_cost("openai", "gpt-does-not-exist", Usage(input_tokens=1))


def test_usage_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Usage(input_tokens=1, bogus=2)  # type: ignore[call-arg]
