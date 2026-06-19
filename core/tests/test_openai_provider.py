"""OpenAIProvider — strict-schema compilation, wire normalization, refusal/parse handling, and
the Anthropic→OpenAI transcript translation. Driven by canned OpenAI payloads (Tier-1, no key).

Tier-2 (@api): real triage + end-to-end, auto-skipped without an OpenAI/Azure key.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

from relay import tools
from relay.models import Faithfulness, Triage
from relay.provider.base import MissingAPIKeyError, ModelStep, ProviderError
from relay.provider.openai import (
    DEFAULT_MODEL,
    OpenAIProvider,
    _to_openai_messages,
)

# --- fakes (the only thing standing in for the network) ----------------------


def _usage(prompt: int = 0, completion: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
    )


def _tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments)
    )


def _message(
    content: str | None = None,
    *,
    refusal: str | None = None,
    tool_calls: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(content=content, refusal=refusal, tool_calls=tool_calls)


def _response(
    message: SimpleNamespace, finish_reason: str = "stop", usage: SimpleNamespace | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=usage if usage is not None else _usage(),
    )


class _FakeCompletions:
    """Returns canned responses in sequence (last repeats); records each create() kwargs."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = responses
        self._i = 0
        self.calls: list[dict[str, Any]] = []

    def create(self, **kw: Any) -> SimpleNamespace:
        self.calls.append(kw)
        resp = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return resp


class _FakeClient:
    def __init__(self, *responses: SimpleNamespace) -> None:
        self.completions = _FakeCompletions(list(responses))
        self.chat = SimpleNamespace(completions=self.completions)


def _triage_json() -> str:
    return json.dumps(
        {
            "intent": "billing_dispute",
            "priority": "high",
            "extracted_fields": {
                "customer_email": "jane@acme.com",
                "order_ref": "A-4471",
                "amount": None,
                "product": None,
            },
            "confidence": "high",
        }
    )


def _provider(*responses: SimpleNamespace, **kw: Any) -> OpenAIProvider:
    return OpenAIProvider(client=_FakeClient(*responses), **kw)


# --- construction / config ---------------------------------------------------


def test_missing_key_raises_catchable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        OpenAIProvider()


def test_unknown_model_rejected() -> None:
    with pytest.raises(ValueError):
        OpenAIProvider(model="gpt-4o", client=_FakeClient())


def test_provider_and_model_attributes() -> None:
    p = _provider()
    assert p.provider == "openai"
    assert p.model == DEFAULT_MODEL == "gpt-5.5"


def test_standard_openai_client_built_from_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    from openai import OpenAI

    p = OpenAIProvider()
    assert isinstance(p._client, OpenAI)


def test_azure_client_selected_when_endpoint_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-test-not-real")
    monkeypatch.setenv("OPENAI_API_VERSION", "2025-01-01-preview")
    from openai import AzureOpenAI

    p = OpenAIProvider()
    assert isinstance(p._client, AzureOpenAI)


def test_azure_endpoint_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        OpenAIProvider()


# --- T1: strict-schema compilation (one schema, both providers) --------------

_FORBIDDEN = {
    "minLength",
    "maxLength",
    "maximum",
    "minimum",
    "exclusiveMaximum",
    "exclusiveMinimum",
}


def _iter_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_nodes(v)
    elif isinstance(node, list):
        for it in node:
            yield from _iter_nodes(it)


def test_triage_is_valid_openai_strict_schema() -> None:
    from relay.models import strict_json_schema

    schema = strict_json_schema(Triage)
    # Every object node: all properties required + additionalProperties:false.
    for node in _iter_nodes(schema):
        if node.get("type") == "object" and "properties" in node:
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"].keys())
    # No forbidden keywords; nullable fields are explicit ["type","null"] unions (spec R2).
    keys: set[str] = set()
    for node in _iter_nodes(schema):
        keys.update(node.keys())
    assert keys.isdisjoint(_FORBIDDEN)
    ef = schema["$defs"]["ExtractedFields"]["properties"]
    assert ef["customer_email"]["type"] == ["string", "null"]
    assert ef["amount"]["type"] == ["number", "null"]
    assert "anyOf" not in json.dumps(schema)  # all unions collapsed to type-arrays


# --- T2: structured_output normalization + parse handling --------------------


def test_structured_output_parses_triage_and_usage() -> None:
    resp = _response(_message(content=_triage_json()), usage=_usage(prompt=300, completion=40))
    p = _provider(resp)
    parsed, usage = p.structured_output("SYS", "TICKET: x", Triage)
    assert isinstance(parsed, Triage)
    assert parsed.intent.value == "billing_dispute"
    assert parsed.extracted_fields.order_ref == "A-4471"
    assert parsed.extracted_fields.amount is None  # null round-trips (E3)
    # Usage maps prompt/completion → input/output; cache buckets stay 0 (Open decision A).
    assert usage.input_tokens == 300 and usage.output_tokens == 40
    assert usage.cache_read_tokens == 0 and usage.cache_creation_tokens == 0
    # The request used strict json_schema and sent NO sampling params.
    kw = p._client.completions.calls[0]
    rf = kw["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "Triage"
    assert not ({"temperature", "top_p", "seed"} & set(kw))


def test_triage_convenience_returns_triage() -> None:
    p = _provider(_response(_message(content=_triage_json())))
    out = p.triage("I was charged twice (order A-4471)")
    assert out.intent.value == "billing_dispute"
    assert out.priority.value == "high"


def test_structured_output_refusal_raises_provider_error() -> None:
    resp = _response(_message(content=None, refusal="I can't help with that."))
    p = _provider(resp)
    with pytest.raises(ProviderError, match="refus"):
        p.structured_output("SYS", "x", Triage)


def test_structured_output_bounded_retry_recovers() -> None:
    # First response is malformed JSON; the bounded retry feeds the error back and recovers.
    bad = _response(_message(content="{not valid json"), usage=_usage(prompt=100, completion=5))
    good = _response(_message(content=_triage_json()), usage=_usage(prompt=120, completion=40))
    p = _provider(bad, good)
    parsed, usage = p.structured_output("SYS", "x", Triage)
    assert isinstance(parsed, Triage)
    # Two attempts were made and usage accumulates across them.
    assert len(p._client.completions.calls) == 2
    assert usage.input_tokens == 220 and usage.output_tokens == 45
    # The retry fed the prior (bad) content + a corrective instruction back to the model.
    retry_messages = p._client.completions.calls[1]["messages"]
    assert any("did not" in m["content"] or "not valid" in m["content"] for m in retry_messages)


def test_structured_output_exhausts_retries_then_surfaces() -> None:
    bad = _response(_message(content="still not json"))
    p = _provider(bad, max_retries=1)
    with pytest.raises(ProviderError, match="failed after 2 attempt"):
        p.structured_output("SYS", "x", Triage)
    assert len(p._client.completions.calls) == 2  # 1 + 1 retry


def test_structured_output_empty_content_is_bounded_retried() -> None:
    empty = _response(_message(content=None), finish_reason="length")
    p = _provider(empty, max_retries=1)
    with pytest.raises(ProviderError, match="empty content"):
        p.structured_output("SYS", "x", Triage)


def test_faithfulness_rides_on_structured_output_unchanged() -> None:
    # Split 04's faithfulness check works on OpenAI for free (it only uses structured_output).
    from relay import faithfulness

    verdict_json = json.dumps(
        {"all_grounded": True, "claims": [{"claim": "refunds in 5-7 days", "label": "SUPPORTED"}]}
    )
    p = _provider(_response(_message(content=verdict_json)))
    verdict, _ = faithfulness.check(
        "Refunds land in 5-7 days.",
        [
            {
                "chunk_id": "kb-refund-001",
                "source": "policy",
                "text": "Refunds in 5-7 business days.",
            }
        ],
        p,
    )
    assert isinstance(verdict, Faithfulness)
    assert verdict.all_grounded is True


# --- T2: step normalization (tool calls / finish_reason / refusal) -----------


def test_step_normalizes_tool_calls_text_and_usage() -> None:
    resp = _response(
        _message(
            content="Looking up the customer.",
            tool_calls=[_tool_call("call_1", "lookup_customer", '{"email": "jane@acme.com"}')],
        ),
        finish_reason="tool_calls",
        usage=_usage(prompt=200, completion=25),
    )
    p = _provider(resp)
    step = p.step(messages=[{"role": "user", "content": "hi"}], tools=tools.tool_schemas())
    assert step.text == "Looking up the customer."  # rationale captured
    assert step.stop_reason == "tool_use"  # normalized to the Anthropic vocabulary
    assert len(step.tool_calls) == 1
    call = step.tool_calls[0]
    assert call.id == "call_1" and call.name == "lookup_customer"
    assert isinstance(call.args, dict) and call.args == {"email": "jane@acme.com"}  # json.loads'd
    assert step.usage.input_tokens == 200 and step.usage.output_tokens == 25


def test_step_finish_reason_length_normalizes_to_max_tokens() -> None:
    resp = _response(_message(content="...partial"), finish_reason="length")
    step = _provider(resp).step(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert step.tool_calls == []
    assert step.stop_reason == "max_tokens"


def test_step_content_filter_refusal_surfaced_not_raised() -> None:
    # A content-filter / refusal must surface as stop_reason='refusal', never crash (§9 parity).
    resp = _response(
        _message(content=None, refusal="This request was blocked."), finish_reason="content_filter"
    )
    step = _provider(resp).step(messages=[], tools=[])
    assert step.stop_reason == "refusal"
    assert step.tool_calls == []
    assert step.stop_details == {"refusal": "This request was blocked."}


def test_step_tool_calls_present_overrides_stop_finish_reason() -> None:
    # Known OpenAI quirk: finish_reason='stop' but tool_calls present → loop must see 'tool_use'.
    resp = _response(
        _message(content=None, tool_calls=[_tool_call("c", "search_kb", '{"query": "refund"}')]),
        finish_reason="stop",
    )
    step = _provider(resp).step(messages=[{"role": "user", "content": "x"}], tools=[])
    assert step.stop_reason == "tool_use" and len(step.tool_calls) == 1


def test_step_malformed_arguments_degrade_to_empty_dict() -> None:
    resp = _response(
        _message(content=None, tool_calls=[_tool_call("c", "update_ticket", "{not: json}")]),
        finish_reason="tool_calls",
    )
    step = _provider(resp).step(messages=[{"role": "user", "content": "x"}], tools=[])
    # No crash; args degrade to {} so the tool layer validates + feeds an error back.
    assert step.tool_calls[0].args == {}


def test_step_prepends_agent_system_and_sends_function_tools() -> None:
    from relay.prompts import AGENT_SYSTEM

    resp = _response(_message(content="done"), finish_reason="stop")
    p = _provider(resp)
    p.step(messages=[{"role": "user", "content": "hi"}], tools=tools.tool_schemas())
    kw = p._client.completions.calls[0]
    assert kw["messages"][0] == {"role": "system", "content": AGENT_SYSTEM}
    # tools are translated to OpenAI function shape.
    assert all(t["type"] == "function" and "parameters" in t["function"] for t in kw["tools"])
    assert not ({"temperature", "top_p", "seed"} & set(kw))  # no sampling params


def test_step_handles_missing_usage() -> None:
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=_message(content="done"), finish_reason="stop")],
        usage=None,
    )
    step = _provider(resp).step(messages=[], tools=[])
    assert step.usage.input_tokens == 0


# --- the Anthropic→OpenAI transcript translation (Split 03 carry-forward) -----


def test_translate_user_string_turn() -> None:
    out = _to_openai_messages([{"role": "user", "content": "hello"}])
    assert out == [{"role": "user", "content": "hello"}]


def test_translate_assistant_tool_use_and_tool_result_roundtrip() -> None:
    transcript = [
        {"role": "user", "content": "TICKET: charged twice"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Looking up the customer."},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "lookup_customer",
                    "input": {"email": "jane@acme.com"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"plan": "Pro"}'},
            ],
        },
    ]
    out = _to_openai_messages(transcript)
    assert out[0] == {"role": "user", "content": "TICKET: charged twice"}
    # assistant turn → content + tool_calls with json-string arguments.
    assistant = out[1]
    assert assistant["role"] == "assistant" and assistant["content"] == "Looking up the customer."
    tc = assistant["tool_calls"][0]
    assert tc["id"] == "tu_1" and tc["type"] == "function"
    assert tc["function"]["name"] == "lookup_customer"
    assert json.loads(tc["function"]["arguments"]) == {"email": "jane@acme.com"}
    # tool_result block → a role:"tool" message keyed by tool_call_id.
    assert out[2] == {"role": "tool", "tool_call_id": "tu_1", "content": '{"plan": "Pro"}'}


def test_translate_assistant_without_text_uses_none_content() -> None:
    out = _to_openai_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t", "name": "search_kb", "input": {"query": "q"}}
                ],
            }
        ]
    )
    assert out[0]["content"] is None and "tool_calls" in out[0]


def test_translate_user_text_block_and_assistant_string_content() -> None:
    # A user turn carrying a text block → a user message; an assistant string turn passes through.
    out = _to_openai_messages(
        [
            {"role": "user", "content": [{"type": "text", "text": "extra context"}]},
            {"role": "assistant", "content": "a plain string summary"},
        ]
    )
    assert out == [
        {"role": "user", "content": "extra context"},
        {"role": "assistant", "content": "a plain string summary"},
    ]


def test_translate_dict_tool_result_content_is_json_encoded() -> None:
    # Defensive: a non-string tool_result content is JSON-encoded (our transcript uses strings,
    # but the translator must not crash on a dict).
    out = _to_openai_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t", "content": {"plan": "Pro"}}
                ],
            }
        ]
    )
    assert out == [{"role": "tool", "tool_call_id": "t", "content": '{"plan": "Pro"}'}]


def test_step_skips_non_function_and_non_dict_args() -> None:
    # A non-function tool call is skipped; a JSON array (non-dict) of args degrades to {}.
    bad_type = SimpleNamespace(
        id="c0", type="custom", function=SimpleNamespace(name="x", arguments="{}")
    )
    array_args = _tool_call("c1", "search_kb", "[1, 2, 3]")
    resp = _response(
        _message(content=None, tool_calls=[bad_type, array_args]), finish_reason="tool_calls"
    )
    step = _provider(resp).step(messages=[{"role": "user", "content": "x"}], tools=[])
    assert len(step.tool_calls) == 1  # the custom-type call was skipped
    assert step.tool_calls[0].args == {}  # the array args degraded to {}


def test_translate_error_tool_result_block() -> None:
    out = _to_openai_messages(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_err",
                        "content": '{"error": "boom", "is_error": true}',
                        "is_error": True,
                    }
                ],
            }
        ]
    )
    assert out == [
        {
            "role": "tool",
            "tool_call_id": "tu_err",
            "content": '{"error": "boom", "is_error": true}',
        }
    ]


# --- Tier-2: real provider (needs OPENAI_API_KEY or Azure creds) --------------

_HAS_KEY = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))

_BILLING = (
    "Hi — I was charged twice for my Pro subscription this month (order #A-4471). "
    "Please refund the duplicate charge. — jane@acme.com"
)


@pytest.mark.api
@pytest.mark.skipif(not _HAS_KEY, reason="needs OPENAI_API_KEY or AZURE_OPENAI_API_KEY")
def test_real_triage_billing_dispute_distribution() -> None:
    # T6 / E1: distributional — billing dispute classified correctly; absent fields return null.
    p = OpenAIProvider()
    results = [p.triage(_BILLING) for _ in range(3)]
    assert all(r.intent.value in {"billing_dispute", "refund_request"} for r in results)
    assert all(r.extracted_fields.customer_email == "jane@acme.com" for r in results)
    # `amount` is absent in the ticket → the null-union schema returns null (E3).
    assert all(r.extracted_fields.amount is None for r in results)


@pytest.mark.api
@pytest.mark.skipif(not _HAS_KEY, reason="needs OPENAI_API_KEY or AZURE_OPENAI_API_KEY")
def test_real_step_usage_positive() -> None:
    p = OpenAIProvider()
    messages = [{"role": "user", "content": "TICKET:\nI was charged twice (order A-4471)."}]
    step = p.step(messages, tools.tool_schemas())
    assert isinstance(step, ModelStep)
    assert step.usage.input_tokens > 0
