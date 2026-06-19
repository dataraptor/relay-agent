"""OpenAI backend for the provider seam (spec §9, Split 05 — facts verified 2026-06-20).

The whole portability point: swapping ``--provider`` changes **one constructor** and nothing
downstream. Only this module knows the OpenAI wire format; it normalizes into the same
``ModelStep`` / ``Usage`` the Anthropic provider produces, so the loop, gate, backend,
faithfulness, eval, and CLI stay provider-agnostic.

What this module does:

- ``structured_output()`` / ``triage()`` — strict structured outputs via
  ``response_format={"type":"json_schema","json_schema":{name, schema, strict:true}}``, parsing
  ``message.content`` into the requested model. A parse failure triggers a **bounded retry** that
  feeds the error back; still failing → a catchable :class:`ProviderError` (never a crash, §20).
- ``step()`` — **one** tool-use turn via Chat Completions with ``tools`` (function calling). It
  reads ``message.tool_calls``, ``json.loads``-es each ``arguments`` string into a
  :class:`NormalizedToolCall`, and captures ``message.content`` as ``ModelStep.text`` (the
  rationale → ``ApprovalRequest.rationale``). It does **not** execute tools or loop — the gate
  intercepts each call (§4/§6).
- **Transcript translation (the Split 03 carry-forward).** The loop persists the in-flight
  transcript in **Anthropic-native** message dicts (``tool_use`` / ``tool_result`` blocks). This
  module translates that canonical shape into OpenAI's ``role:"assistant"+tool_calls`` /
  ``role:"tool"`` shape at its own seam — the loop and ``agent.py`` stay unchanged.
- **finish_reason normalization** maps OpenAI's ``stop``/``length``/``content_filter``/
  ``tool_calls`` onto the same vocabulary the Anthropic provider uses, so the loop branches
  identically.
- **Usage** maps ``prompt_tokens``/``completion_tokens`` onto ``input``/``output``; the cache
  buckets are left **0** (OpenAI's ``prompt_tokens`` already includes any cached tokens and has
  no cache-write surcharge — Open decision A; see ``cost.py``). The asymmetry is honest, not faked.
- **Sampling-param hygiene.** No ``temperature``/``top_p``/``seed`` are sent: gpt-5.x reasoning
  models reject non-default sampling, and determinism is best-effort regardless (§12 → results
  are reported distributionally). The construction path is its own class, so an OpenAI-only param
  can never leak onto the Anthropic call.

**Azure or api.openai.com.** If ``AZURE_OPENAI_ENDPOINT`` is set the client is an ``AzureOpenAI``
(the deployment name is the ``model``); otherwise a standard ``OpenAI`` client keyed by
``OPENAI_API_KEY``. Either way the normalization below is identical.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, ValidationError

from ..models import Triage, strict_json_schema
from ..prompts import AGENT_SYSTEM, TRIAGE_SYSTEM, triage_user_content
from .base import (
    MissingAPIKeyError,
    ModelStep,
    NormalizedToolCall,
    ProviderError,
    Usage,
)

#: Pinned OpenAI model id (verified 2026-06-20). For Azure this doubles as the deployment name.
DEFAULT_MODEL = "gpt-5.5"
OPENAI_MODELS = frozenset({"gpt-5.5"})

#: Default Azure API version if the environment does not pin one.
_DEFAULT_AZURE_API_VERSION = "2025-01-01-preview"

#: finish_reason → the normalized stop-reason vocabulary the Anthropic provider emits, so the
#: loop's branching (``stop_reason == "refusal"``, end-of-turn) is identical across providers.
_FINISH_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "refusal",
    "tool_calls": "tool_use",
}


class OpenAIProvider:
    """``ProviderClient`` backed by the official ``openai`` SDK (OpenAI or Azure OpenAI).

    Pass ``client`` to inject a stub/mock (tests drive the no-key-reachable normalization paths
    this way). Otherwise the client is built from the environment: Azure when
    ``AZURE_OPENAI_ENDPOINT`` is present, else standard OpenAI. A missing key raises
    :class:`MissingAPIKeyError` (catchable, no stack-trace crash — parity with Anthropic).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        max_completion_tokens: int = 4096,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        if model not in OPENAI_MODELS:
            raise ValueError(
                f"unknown OpenAI model {model!r}; pin one of {sorted(OPENAI_MODELS)} "
                "(its pricing must also be pinned in cost.py)"
            )
        self.provider = "openai"
        self.model = model
        self._max_completion_tokens = max_completion_tokens
        self._max_retries = max_retries

        if client is not None:
            self._client = client
            return
        self._client = _build_client(api_key)

    # -- structured outputs ---------------------------------------------------

    def structured_output(
        self, system: str, user: str, schema_model: type[BaseModel]
    ) -> tuple[BaseModel, Usage]:
        """One strict ``json_schema`` call → (validated model, accumulated usage).

        Bounded-retries a parse/validation failure (feeding the error back); surfaces an honest
        :class:`ProviderError` if it still cannot parse, and a refusal as a ``ProviderError``.
        """
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_model.__name__,
                "schema": strict_json_schema(schema_model),
                "strict": True,
            },
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        total = Usage()
        last_error = "unknown error"
        for attempt in range(self._max_retries + 1):
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format=response_format,
                max_completion_tokens=self._max_completion_tokens,
            )
            total = total + _usage(resp)
            choice = resp.choices[0]
            message = choice.message
            refusal = getattr(message, "refusal", None)
            if refusal:
                raise ProviderError(f"structured output refused (OpenAI refusal): {refusal}")
            content = getattr(message, "content", None)
            if content:
                try:
                    parsed = schema_model.model_validate_json(content)
                    return parsed, total
                except ValidationError as exc:
                    last_error = f"schema validation failed: {exc.errors(include_url=False)}"
            else:
                last_error = f"empty content (finish_reason={choice.finish_reason!r})"
            if attempt < self._max_retries:
                # Feed the failure back and ask for a corrected, schema-valid JSON object.
                messages = messages + [
                    {"role": "assistant", "content": content or ""},
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response was not valid ({last_error}). "
                            "Return ONLY a JSON object matching the required schema."
                        ),
                    },
                ]
        raise ProviderError(
            f"structured output failed after {self._max_retries + 1} attempt(s): {last_error}"
        )

    def triage(self, ticket: str) -> Triage:
        parsed, _ = self.structured_output(TRIAGE_SYSTEM, triage_user_content(ticket), Triage)
        assert isinstance(parsed, Triage)  # the strict schema guarantees the shape
        return parsed

    # -- one tool-use turn ----------------------------------------------------

    def step(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelStep:
        """One Chat Completions turn. ``AGENT_SYSTEM`` is prepended as the system message; the
        running (Anthropic-native) transcript is translated to OpenAI shape at this seam."""
        oai_messages = [{"role": "system", "content": AGENT_SYSTEM}] + _to_openai_messages(messages)
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=oai_messages,
            tools=[_to_openai_tool(t) for t in tools],
            max_completion_tokens=self._max_completion_tokens,
        )
        usage = _usage(resp)
        choice = resp.choices[0]
        message = choice.message
        refusal = getattr(message, "refusal", None)
        if refusal:
            # Surface, do not read tool calls blindly on a refusal (§9 parity).
            return ModelStep(
                text=refusal,
                tool_calls=[],
                usage=usage,
                stop_reason="refusal",
                stop_details={"refusal": refusal},
            )
        tool_calls = _normalize_tool_calls(getattr(message, "tool_calls", None))
        return ModelStep(
            text=getattr(message, "content", None) or "",
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=_normalize_stop_reason(choice.finish_reason, tool_calls),
        )


# --- client construction (Azure or standard OpenAI) --------------------------


def _build_client(api_key: str | None) -> Any:
    """Build an Azure or standard OpenAI client from the environment.

    Azure is selected when ``AZURE_OPENAI_ENDPOINT`` is set (the project's gpt-5.5 deployment);
    otherwise a standard ``OpenAI`` client. Imported locally so ``import relay`` stays light.
    """
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if azure_endpoint:
        key = api_key or os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise MissingAPIKeyError(
                "AZURE_OPENAI_API_KEY is not set (AZURE_OPENAI_ENDPOINT is). Export it (or pass "
                "api_key=...) to use the Azure OpenAI provider."
            )
        from openai import AzureOpenAI

        return AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=key,
            api_version=os.environ.get("OPENAI_API_VERSION", _DEFAULT_AZURE_API_VERSION),
        )

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise MissingAPIKeyError(
            "OPENAI_API_KEY is not set. Export it (or pass api_key=...) to use the OpenAI "
            "provider (or set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY for Azure)."
        )
    from openai import OpenAI

    return OpenAI(api_key=key)


# --- normalization helpers (the only place the OpenAI wire format is read) ----


def _usage(resp: Any) -> Usage:
    """Map OpenAI usage onto the normalized buckets (cache buckets stay 0 — see ``cost.py``)."""
    u = getattr(resp, "usage", None)
    if u is None:
        return Usage()
    return Usage(
        input_tokens=getattr(u, "prompt_tokens", 0) or 0,
        output_tokens=getattr(u, "completion_tokens", 0) or 0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
    )


def _normalize_stop_reason(finish_reason: str | None, tool_calls: list[NormalizedToolCall]) -> str:
    """Normalize ``finish_reason`` to the Anthropic vocabulary.

    A turn that proposed tool calls is ``tool_use`` even if ``finish_reason`` came back ``stop``
    (a known OpenAI quirk) — the presence of ``tool_calls`` is authoritative for the loop.
    """
    if tool_calls:
        return "tool_use"
    return _FINISH_MAP.get(finish_reason or "", finish_reason or "end_turn")


def _normalize_tool_calls(raw: Any) -> list[NormalizedToolCall]:
    """Parse ``message.tool_calls`` into normalized calls; ``arguments`` via ``json.loads``.

    A malformed ``arguments`` string degrades to ``{}`` (the tool layer then validates and feeds
    back an ``is_error`` result, so the model can correct itself — never a crash)."""
    calls: list[NormalizedToolCall] = []
    for tc in raw or []:
        if getattr(tc, "type", "function") != "function":
            continue
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        raw_args = getattr(fn, "arguments", None) or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append(NormalizedToolCall(id=tc.id, name=fn.name, args=args))
    return calls


def _to_openai_tool(tool_schema: dict[str, Any]) -> dict[str, Any]:
    """Anthropic-native tool schema → OpenAI function-tool shape (non-strict, per Split 02)."""
    return {
        "type": "function",
        "function": {
            "name": tool_schema["name"],
            "description": tool_schema.get("description", ""),
            "parameters": tool_schema["input_schema"],
        },
    }


def _block_text(content: Any) -> str:
    """A tool_result block's content is already a string (JSON or text) in our transcript."""
    return content if isinstance(content, str) else json.dumps(content)


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the canonical (Anthropic-native) transcript into OpenAI chat messages.

    - user string turn → ``{"role":"user","content": <text>}``
    - assistant turn (text + ``tool_use`` blocks) → ``{"role":"assistant","content": <text|None>,
      "tool_calls":[{id, type:function, function:{name, arguments:<json str>}}]}``
    - user turn of ``tool_result`` blocks → one ``{"role":"tool","tool_call_id":..., ...}`` per
      block (preserving order, so every prior tool call is answered).
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
                continue
            for block in content or []:
                btype = block.get("type")
                if btype == "tool_result":
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": _block_text(block.get("content", "")),
                        }
                    )
                elif btype == "text":
                    out.append({"role": "user", "content": block.get("text", "")})
        elif role == "assistant":
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
                continue
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in content or []:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append(
                        {
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        }
                    )
            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(text_parts) or None,
            }
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            out.append(assistant)
    return out
