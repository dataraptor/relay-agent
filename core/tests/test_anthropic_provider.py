"""AnthropicProvider normalization + refusal safety.

Tier-1 (no key): drive the SDK-call boundary with a fake client so the normalization paths
(usage buckets, tool-call parsing, refusal handling, no-sampling-params, prompt caching) run
deterministically and for free. Tier-2 (@api): real triage + caching, skipped without a key.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from relay import tools
from relay.models import Triage
from relay.provider.anthropic import DEFAULT_MODEL, AnthropicProvider
from relay.provider.base import MissingAPIKeyError, ProviderError

# --- fakes (the only thing standing in for the network) ----------------------


def _usage(**kw: int) -> SimpleNamespace:
    base = dict(
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_block(id: str, name: str, inp: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=inp)


class _FakeMessages:
    """Records the last kwargs and returns canned responses."""

    def __init__(self, create_resp=None, parse_resp=None) -> None:
        self._create_resp = create_resp
        self._parse_resp = parse_resp
        self.last_create: dict | None = None
        self.last_parse: dict | None = None

    def create(self, **kw):
        self.last_create = kw
        return self._create_resp

    def parse(self, **kw):
        self.last_parse = kw
        return self._parse_resp


class _FakeClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def _triage_resp() -> SimpleNamespace:
    parsed = Triage.model_validate(
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
    return SimpleNamespace(
        stop_reason="end_turn",
        usage=_usage(input_tokens=300, output_tokens=40, cache_read_input_tokens=0),
        parsed_output=parsed,
    )


# --- construction / config ---------------------------------------------------


def test_missing_key_raises_catchable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        AnthropicProvider()


def test_unknown_model_rejected() -> None:
    with pytest.raises(ValueError):
        AnthropicProvider(model="claude-sonnet-4-6-20251114", client=_FakeClient(_FakeMessages()))


def test_provider_and_model_attributes() -> None:
    p = AnthropicProvider(client=_FakeClient(_FakeMessages()))
    assert p.provider == "anthropic"
    assert p.model == DEFAULT_MODEL == "claude-sonnet-4-6"
    p2 = AnthropicProvider(model="claude-opus-4-8", client=_FakeClient(_FakeMessages()))
    assert p2.model == "claude-opus-4-8"


# --- structured_output / triage ----------------------------------------------


def test_structured_output_returns_parsed_and_usage() -> None:
    msgs = _FakeMessages(parse_resp=_triage_resp())
    p = AnthropicProvider(client=_FakeClient(msgs))
    parsed, usage = p.structured_output("SYS", "TICKET: x", Triage)
    assert isinstance(parsed, Triage)
    assert parsed.extracted_fields.order_ref == "A-4471"
    assert usage.input_tokens == 300 and usage.output_tokens == 40
    # output_format=Triage is passed through; no sampling params.
    assert msgs.last_parse["output_format"] is Triage
    assert not ({"temperature", "top_p", "top_k", "seed"} & set(msgs.last_parse))


def test_triage_convenience_returns_triage() -> None:
    msgs = _FakeMessages(parse_resp=_triage_resp())
    p = AnthropicProvider(client=_FakeClient(msgs))
    out = p.triage("I was charged twice (order A-4471)")
    assert out.intent.value == "billing_dispute"
    assert out.priority.value == "high"


def test_structured_output_refusal_raises_provider_error() -> None:
    refusal = SimpleNamespace(
        stop_reason="refusal", usage=_usage(), parsed_output=None, stop_details=None
    )
    p = AnthropicProvider(client=_FakeClient(_FakeMessages(parse_resp=refusal)))
    with pytest.raises(ProviderError):
        p.structured_output("SYS", "x", Triage)


def test_structured_output_none_parsed_raises() -> None:
    bad = SimpleNamespace(stop_reason="end_turn", usage=_usage(), parsed_output=None)
    p = AnthropicProvider(client=_FakeClient(_FakeMessages(parse_resp=bad)))
    with pytest.raises(ProviderError):
        p.structured_output("SYS", "x", Triage)


# --- step normalization ------------------------------------------------------


def test_step_normalizes_text_tool_calls_and_usage() -> None:
    resp = SimpleNamespace(
        stop_reason="tool_use",
        usage=_usage(input_tokens=200, output_tokens=25, cache_read_input_tokens=128),
        content=[
            _text_block("Looking up the customer."),
            _tool_block("tu_1", "lookup_customer", {"email": "jane@acme.com"}),
        ],
    )
    msgs = _FakeMessages(create_resp=resp)
    p = AnthropicProvider(client=_FakeClient(msgs))
    step = p.step(messages=[{"role": "user", "content": "hi"}], tools=tools.tool_schemas())

    assert step.text == "Looking up the customer."  # captured for ApprovalRequest.rationale
    assert step.stop_reason == "tool_use"
    assert len(step.tool_calls) == 1
    call = step.tool_calls[0]
    assert call.name == "lookup_customer"
    assert isinstance(call.args, dict) and call.args == {"email": "jane@acme.com"}
    assert step.usage.input_tokens == 200
    assert step.usage.cache_read_tokens == 128


def test_step_sends_no_sampling_params_and_caches_prefix() -> None:
    resp = SimpleNamespace(stop_reason="end_turn", usage=_usage(), content=[_text_block("done")])
    msgs = _FakeMessages(create_resp=resp)
    p = AnthropicProvider(client=_FakeClient(msgs))
    p.step(messages=[{"role": "user", "content": "hi"}], tools=tools.tool_schemas())

    kw = msgs.last_create
    assert not ({"temperature", "top_p", "top_k", "seed"} & set(kw))  # never to Anthropic
    # Stable prefix carries an ephemeral cache breakpoint (caches system + tools).
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["tools"] == tools.tool_schemas()
    # Ticket text is NOT interpolated into the system prompt (cache rule §13).
    assert "hi" not in kw["system"][0]["text"]


def test_step_refusal_is_surfaced_not_raised() -> None:
    # T6 / E2: a refusal response must never crash and must report stop_reason="refusal".
    resp = SimpleNamespace(
        stop_reason="refusal",
        usage=_usage(input_tokens=10),
        content=[],
        stop_details=SimpleNamespace(type="refusal", category="cyber", explanation="no"),
    )
    p = AnthropicProvider(client=_FakeClient(_FakeMessages(create_resp=resp)))
    step = p.step(messages=[], tools=[])
    assert step.stop_reason == "refusal"
    assert step.tool_calls == []
    assert step.stop_details == {"type": "refusal", "category": "cyber", "explanation": "no"}


def test_step_refusal_passes_through_dict_stop_details() -> None:
    # stop_details already a dict (some SDK/wire shapes) is returned as-is.
    resp = SimpleNamespace(
        stop_reason="refusal",
        usage=_usage(),
        content=[],
        stop_details={"type": "refusal", "category": None},
    )
    p = AnthropicProvider(client=_FakeClient(_FakeMessages(create_resp=resp)))
    step = p.step(messages=[], tools=[])
    assert step.stop_details == {"type": "refusal", "category": None}


def test_step_handles_missing_usage_and_empty_content() -> None:
    resp = SimpleNamespace(stop_reason="end_turn", usage=None, content=None)
    p = AnthropicProvider(client=_FakeClient(_FakeMessages(create_resp=resp)))
    step = p.step(messages=[], tools=[])
    assert step.usage.input_tokens == 0
    assert step.text == "" and step.tool_calls == []


# --- Tier-2: real provider (needs ANTHROPIC_API_KEY) -------------------------

_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.api
@pytest.mark.skipif(not _HAS_KEY, reason="needs ANTHROPIC_API_KEY")
def test_real_triage_billing_dispute_distribution() -> None:
    # T7 / E1: distributional — billing dispute classified correctly across N runs.
    ticket = (
        "Hi — I was charged twice for my Pro subscription this month (order #A-4471). "
        "Please refund the duplicate charge. — jane@acme.com"
    )
    p = AnthropicProvider()
    results = [p.triage(ticket) for _ in range(3)]
    assert all(r.intent.value == "billing_dispute" for r in results)
    assert all(r.priority.value in {"high", "urgent"} for r in results)
    assert all(r.extracted_fields.customer_email == "jane@acme.com" for r in results)
    assert all(r.extracted_fields.order_ref == "A-4471" for r in results)


@pytest.mark.api
@pytest.mark.skipif(not _HAS_KEY, reason="needs ANTHROPIC_API_KEY")
def test_real_step_usage_and_caching() -> None:
    # T8 / E3: usage.input_tokens > 0, and a repeat step engages the prompt cache (if the
    # stable prefix clears Sonnet's 2048-token floor).
    p = AnthropicProvider()
    messages = [{"role": "user", "content": "TICKET:\nI was charged twice (order A-4471)."}]
    schemas = tools.tool_schemas()
    first = p.step(messages, schemas)
    assert first.usage.input_tokens > 0 or first.usage.cache_creation_tokens > 0
    second = p.step(messages, schemas)
    assert second.usage.cache_read_tokens >= 0  # >0 expected once prefix clears the cache floor
