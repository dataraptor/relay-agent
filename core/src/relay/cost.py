"""Cache-aware cost accounting keyed by ``(provider, model_id)`` (spec §13, API conformance).

``$/ticket`` = SUM(``llm_calls.cost_usd``) over a run (triage + each loop step + faithfulness).
This module owns the pricing table and the 3-bucket cache-aware formula. Anthropic pricing is
pinned (verified 2026-06-19); OpenAI pricing is pinned (verified 2026-06-20, Split 05 — see
``PRICING`` for the source). Any ``(provider, model)`` with no pinned entry *raises* (never a
silent 0) so nobody ships a fabricated number.

**OpenAI cache asymmetry (Open decision A, §05).** OpenAI's ``prompt_tokens`` already *includes*
any cached prompt tokens (they are a subset, not a separate bucket) and OpenAI has no
cache-*write* surcharge — so the Anthropic cache multipliers below (``1.25×`` write / ``0.10×``
read) must **never** be applied to OpenAI usage. The OpenAI provider therefore reports
``cache_read_tokens == cache_creation_tokens == 0`` and prices the full prompt as ``input_tokens``;
prompt caching is simply *not credited* on the OpenAI path in v1 (honest, not faked).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

#: Pricing per million tokens (MTok): (input_per_mtok, output_per_mtok), in USD.
#: Anthropic — pinned from the spec's API-conformance section (verified 2026-06-19).
#: OpenAI — pinned for the gpt-5.5 deployment used by this project (verified 2026-06-20 against
#: aipricing.guru/openai-pricing and cross-checked via openrouter.ai/openai/gpt-5.5 +
#: morphllm.com/openai-api-pricing): input $5.00 / cached-input $0.50 / output $30.00 per MTok.
#: We do not credit the cached-input rate (Open decision A) — see the module docstring.
PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-opus-4-8"): (5.0, 25.0),
    ("anthropic", "claude-sonnet-4-6"): (3.0, 15.0),
    ("anthropic", "claude-haiku-4-5"): (1.0, 5.0),
    ("openai", "gpt-5.5"): (5.0, 30.0),
}

#: Prompt-cache minimum prefix sizes in tokens (§13). A system+tools prefix below the floor
#: simply won't cache (``cache_creation_tokens == 0``) — expected, not a regression.
CACHE_FLOOR_TOKENS: dict[str, int] = {
    "claude-opus-4-8": 4096,
    "claude-haiku-4-5": 4096,
    "claude-sonnet-4-6": 2048,
}

# Anthropic cache multipliers vs the base input rate (§13).
_CACHE_WRITE_MULT = 1.25  # writing a cache entry costs ~1.25x input
_CACHE_READ_MULT = 0.10  # reading a cached prefix costs ~0.10x input


class Usage(BaseModel):
    """Token buckets for one model inference — mirrors the ``llm_calls`` columns (§11).

    ``input_tokens`` is the *fresh* (uncached) input count; Anthropic already excludes the
    cache buckets from it, so the formula below never double-counts.
    """

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        """Bucket-wise sum so a run can accumulate triage + each loop step + faithfulness."""
        if not isinstance(other, Usage):
            return NotImplemented
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
        )

    def __radd__(self, other: object) -> Usage:
        """Let ``sum(usages)`` work (it starts from the int ``0``)."""
        if other == 0:
            return self
        return self.__add__(other)  # type: ignore[arg-type]


def price(provider: str, model_id: str) -> tuple[float, float]:
    """Return ``(input_per_mtok, output_per_mtok)`` for a known ``(provider, model_id)``.

    An unknown pair (either provider) raises ``KeyError`` — never a silent 0 and never a
    fabricated number.
    """
    try:
        return PRICING[(provider, model_id)]
    except KeyError as exc:
        raise KeyError(
            f"No pricing pinned for (provider={provider!r}, model_id={model_id!r}). "
            f"Known: {sorted(PRICING.keys())}"
        ) from exc


def compute_cost(provider: str, model_id: str, usage: Usage) -> float:
    """Compute the USD cost of one inference with the 3-bucket cache-aware formula (§13).

    usd = ( input_tokens          * in_rate
          + cache_creation_tokens * in_rate * 1.25   # cache write
          + cache_read_tokens     * in_rate * 0.10   # cache read
          + output_tokens         * out_rate ) / 1e6
    """
    in_rate, out_rate = price(provider, model_id)
    usd = (
        usage.input_tokens * in_rate
        + usage.cache_creation_tokens * in_rate * _CACHE_WRITE_MULT
        + usage.cache_read_tokens * in_rate * _CACHE_READ_MULT
        + usage.output_tokens * out_rate
    ) / 1_000_000
    return usd
