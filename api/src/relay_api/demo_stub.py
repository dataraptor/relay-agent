"""Offline demo / CI stub path (Split 10 R1, R4).

When ``RELAY_STUB=1`` (or no provider key is configured), ``/handle`` is served by a deterministic
:class:`~relay.StubProvider` instead of a live model. This is **the same StubProvider the test
suite uses**, surfaced over HTTP for two honest, documented purposes:

* **A no-key reviewer demo (R1).** A reviewer with neither an Anthropic nor an OpenAI key can still
  see the whole money moment — triage → reads → a cited reply → the gate *pauses* → Approve fires
  the write — driven by canned data. It never calls a model.
* **A deterministic cross-stack e2e (R4).** The Node cross-stack test
  (`app/tests/crossstack.test.js`) boots a real ``uvicorn`` server in this mode so the
  never-acts-without-approval invariant can be asserted through core→api→app over real HTTP,
  for free, on every commit.

**Honesty (the spec's half-the-pitch rule, §13/§17).** This is *canned*, not a real run: there
is no model call and no network, so latency is ~0 and ``$/ticket`` is a *representative* figure
computed from canned token counts at the real per-provider rates (so the cost panel renders sanely).
It is labelled ``stub: true`` on ``/health`` and ``/config`` so the UI and a reviewer can tell it
apart from a live run. For live, multi-scenario behaviour, set a provider key — the demo prefers a
live provider whenever one is available.
"""

from __future__ import annotations

from relay import ModelStep, StubProvider
from relay.cost import Usage
from relay.models import ClaimVerdict, ExtractedFields, Faithfulness, Triage
from relay.provider import anthropic as _anthropic
from relay.provider import openai as _openai
from relay.provider.base import NormalizedToolCall

#: Canned per-call token usage (input, output). Real enough that priced at the provider's real
#: rate the $/ticket lands in the right order of magnitude; small enough to stay honest.
_USAGE = Usage(input_tokens=320, output_tokens=110)


def _default_model(provider: str) -> str:
    if provider == "openai":
        return _openai.DEFAULT_MODEL
    return _anthropic.DEFAULT_MODEL


def _call(tool: str, **args: object) -> NormalizedToolCall:
    return NormalizedToolCall(id=f"tc_{tool}", name=tool, args=dict(args))


def _step(text: str, *calls: NormalizedToolCall) -> ModelStep:
    return ModelStep(text=text, tool_calls=list(calls), usage=_USAGE)


def _billing_script() -> tuple[Triage, list[ModelStep], list]:
    """The headline money demo: read the customer + policy, draft a cited reply, then **propose**
    a status update (which the gate pauses). Mirrors `core/examples/billing_dispute.json`."""
    triage = Triage(
        intent="billing_dispute",
        priority="high",
        extracted_fields=ExtractedFields(
            customer_email="jane@acme.com", order_ref="A-4471", amount=None, product="Pro"
        ),
        confidence="high",
    )
    steps = [
        _step("Looking up the customer.", _call("lookup_customer", email="jane@acme.com")),
        _step(
            "Checking the refund policy.",
            _call("search_kb", query="duplicate charge refund policy"),
        ),
        _step(
            "Drafting a grounded reply citing the duplicate-charge policy.",
            _call(
                "draft_reply",
                body=(
                    "Hi Jane — we've confirmed the duplicate charge on order #A-4471 and will "
                    "refund it in full within 5-7 business days once verified."
                ),
                citations=["kb-refund-001"],
            ),
        ),
        _step(
            "The duplicate charge is verified; I'd like to mark the ticket pending_refund — "
            "approving this is a state change, so it needs your sign-off.",
            _call(
                "update_ticket", ticket_id="T-1042", status="pending_refund", note="dup verified"
            ),
        ),
    ]
    faith = [
        Faithfulness(
            all_grounded=True,
            claims=[
                ClaimVerdict(
                    claim="Duplicate charges are refunded in full within 5-7 business days "
                    "once verified.",
                    label="SUPPORTED",
                )
            ],
        )
    ]
    return triage, steps, faith


def _injection_script() -> tuple[Triage, list[ModelStep], list]:
    """The trust frame: the ticket tries to *force* an un-approved refund. Even if the model is
    manipulated into proposing the write, the gate is **code** — it pauses, and no write fires
    without an explicit decision. Mirrors `core/examples/injection.json` (target ticket T-2001)."""
    triage = Triage(
        intent="refund_request",
        priority="normal",
        extracted_fields=ExtractedFields(
            customer_email=None, order_ref=None, amount=None, product=None
        ),
        confidence="low",
    )
    steps = [
        _step(
            "The ticket demands an immediate refund and to close without approval. That is a "
            "state change — I will propose it for human approval rather than act on the demand.",
            _call("update_ticket", ticket_id="T-2001", status="pending_refund"),
        ),
    ]
    return triage, steps, []


def build_demo_stub(ticket: str, provider: str, model: str | None) -> StubProvider:
    """A deterministic, no-network provider scripted for the worked demo (R1/R4).

    The injection ticket gets the gate-holds script; every other ticket replays the billing money
    demo. Reports the *requested* ``provider``/``model`` so the cost panel prices the canned tokens
    at the real per-provider rate (a representative $/ticket, not a billed run).
    """
    low = ticket.lower()
    is_injection = "ignore your previous instructions" in low or "attacker@evil.com" in low
    triage, steps, faith = _injection_script() if is_injection else _billing_script()
    return StubProvider(
        triage_result=triage,
        structured_results=faith,
        steps=steps,
        provider=provider,
        model=model or _default_model(provider),
        usage=_USAGE,
    )
