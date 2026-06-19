"""Anthropic backend for the provider seam (spec §9, API conformance — verified 2026-06-19).

What this module does and, just as importantly, what it does **not**:

- ``triage()`` / ``structured_output()`` — native structured outputs via
  ``client.messages.parse(..., output_format=Model)`` → validated model + normalized usage.
- ``step()`` — **one** tool-use turn via ``client.messages.create(..., tools=[...])``,
  reading ``tool_use`` blocks. It does **not** execute tools or loop (that is Split 03's manual
  loop; the gate must intercept each call before execution).
- **Refusal-safe:** ``stop_reason == "refusal"`` returns a ``ModelStep(stop_reason="refusal")``
  with whatever partial text exists and ``stop_details`` — never raises.
- **No sampling params:** ``temperature``/``top_p``/``top_k``/``seed`` are never sent
  (rejected with 400 on Opus 4.8; banned project-wide). LLM output is not byte-reproducible.
- **Prompt caching:** ``cache_control:{type:"ephemeral"}`` on the stable system+tools prefix
  (tools render before system, so the system breakpoint caches both). Ticket text goes in the
  user turn, after the breakpoint (never interpolated into the system prompt — §13).
- Writes **no** ``llm_calls`` rows; it returns normalized ``Usage`` for Split 03 to price.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel

from ..models import Triage
from ..prompts import AGENT_SYSTEM, TRIAGE_SYSTEM, triage_user_content
from .base import (
    MissingAPIKeyError,
    ModelStep,
    NormalizedToolCall,
    ProviderError,
    Usage,
)

#: Pinned Anthropic model IDs (exact strings, no date suffix — §3). Default = the loop model.
DEFAULT_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MODELS = frozenset({"claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"})


class AnthropicProvider:
    """``ProviderClient`` backed by the official ``anthropic`` SDK.

    Pass ``client`` to inject a stub/mock (tests drive the no-key-reachable normalization paths
    this way). Otherwise an API key is read from ``api_key`` or ``ANTHROPIC_API_KEY``; a missing
    key raises :class:`MissingAPIKeyError` (catchable, no stack-trace crash).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = 2048,
        client: Any | None = None,
    ) -> None:
        if model not in ANTHROPIC_MODELS:
            raise ValueError(
                f"unknown Anthropic model {model!r}; pin one of {sorted(ANTHROPIC_MODELS)} "
                "(exact strings, no date suffix)"
            )
        self.provider = "anthropic"
        self.model = model
        self._max_tokens = max_tokens

        if client is not None:
            self._client = client
            return
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set. Export it (or pass api_key=...) to use the "
                "Anthropic provider."
            )
        import anthropic  # local import keeps `import relay` light when no key is present

        self._client = anthropic.Anthropic(api_key=key)

    # -- structured outputs ---------------------------------------------------

    def structured_output(
        self, system: str, user: str, schema_model: type[BaseModel]
    ) -> tuple[BaseModel, Usage]:
        """One ``messages.parse`` call → (validated model, normalized usage)."""
        resp = self._client.messages.parse(
            model=self.model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema_model,
        )
        if getattr(resp, "stop_reason", None) == "refusal":
            raise ProviderError(
                f"structured output refused (stop_reason=refusal): {_stop_details(resp)}"
            )
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise ProviderError("structured output returned no parsed result")
        return parsed, _usage(resp)

    def triage(self, ticket: str) -> Triage:
        parsed, _ = self.structured_output(TRIAGE_SYSTEM, triage_user_content(ticket), Triage)
        assert isinstance(parsed, Triage)  # output_format=Triage guarantees the type
        return parsed

    # -- one tool-use turn ----------------------------------------------------

    def step(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelStep:
        """One ``messages.create`` turn. ``AGENT_SYSTEM`` is the stable cached prefix; the
        running transcript (ticket + prior tool results) is ``messages``."""
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            system=_cached_system(AGENT_SYSTEM),
            tools=tools,
            messages=messages,
        )
        usage = _usage(resp)
        stop_reason = getattr(resp, "stop_reason", "") or ""
        if stop_reason == "refusal":
            # Do not read content blindly on a refusal — surface, don't crash (§9).
            return ModelStep(
                text=_text_of(resp),
                tool_calls=[],
                usage=usage,
                stop_reason="refusal",
                stop_details=_stop_details(resp),
            )
        tool_calls = [
            NormalizedToolCall(id=block.id, name=block.name, args=dict(block.input or {}))
            for block in (resp.content or [])
            if getattr(block, "type", None) == "tool_use"
        ]
        return ModelStep(
            text=_text_of(resp),
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop_reason,
        )


# --- normalization helpers (the only place the wire format is read) ----------


def _cached_system(text: str) -> list[dict[str, Any]]:
    """The stable system prefix with an ephemeral cache breakpoint (caches system + tools)."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _text_of(resp: Any) -> str:
    """Concatenate the assistant text blocks (the rationale around any tool calls)."""
    return "".join(
        block.text
        for block in (getattr(resp, "content", None) or [])
        if getattr(block, "type", None) == "text"
    )


def _usage(resp: Any) -> Usage:
    """Map the response's usage fields onto the normalized token buckets."""
    u = getattr(resp, "usage", None)
    if u is None:
        return Usage()
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def _stop_details(resp: Any) -> dict[str, Any] | None:
    """Normalize ``stop_details`` (populated only on refusal) to a plain dict."""
    details = getattr(resp, "stop_details", None)
    if details is None:
        return None
    if isinstance(details, dict):
        return details
    return {
        "type": getattr(details, "type", None),
        "category": getattr(details, "category", None),
        "explanation": getattr(details, "explanation", None),
    }
