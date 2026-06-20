"""Generate canned RunView fixtures for the Split 08 mapper tests.

These are **real** RunView projections from the Split 07 API (driven by the no-network
``StubProvider`` + the live ledger projection), dumped to JSON so the Node/Tier-1 mapper tests
run against authentic backend output — not a hand-written guess at the contract. Re-run after any
RunView contract change:  ``python app/tests/_gen_fixtures.py``  (from the repo root).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from relay import ModelStep, StubProvider
from relay.cost import Usage
from relay.models import ExtractedFields, Triage
from relay.provider.base import NormalizedToolCall
from relay_api.app import create_app, provider_dependency
from relay_api.runs import RunStore

OUT = Path(__file__).resolve().parent / "fixtures"


def _call(tool: str, **args: object) -> NormalizedToolCall:
    return NormalizedToolCall(id=f"tc_{tool}", name=tool, args=dict(args))


def _step(text: str, *calls: NormalizedToolCall) -> ModelStep:
    return ModelStep(
        text=text,
        tool_calls=list(calls),
        usage=Usage(input_tokens=120, output_tokens=24),
    )


def _stub(triage: Triage, steps: list[ModelStep]) -> StubProvider:
    return StubProvider(
        triage_result=triage,
        steps=steps,
        provider="anthropic",
        model="claude-sonnet-4-6",
        usage=Usage(input_tokens=80, output_tokens=16),
    )


def _client(store: RunStore, stub: StubProvider) -> TestClient:
    app = create_app(store=store)
    app.dependency_overrides[provider_dependency] = lambda: stub
    return TestClient(app)


# -- scenarios ---------------------------------------------------------------

BILLING_TRIAGE = Triage(
    intent="billing_dispute",
    priority="high",
    extracted_fields=ExtractedFields(
        customer_email="jane@acme.com", order_ref="A-4471", amount=None, product="Pro"
    ),
    confidence="medium",
)

BILLING_STEPS = [
    _step("Looking up the customer.", _call("lookup_customer", email="jane@acme.com")),
    _step("Checking refund policy.", _call("search_kb", query="duplicate charge refund policy")),
    _step(
        "Drafting a cited reply.",
        _call(
            "draft_reply",
            body="Hi Jane — we've confirmed the duplicate charge on order A-4471 and will "
            "refund it in full within 5-7 business days.",
            citations=["kb-refund-001"],
        ),
    ),
    _step(
        "Proposing a status update for your approval.",
        _call("update_ticket", ticket_id="T-1042", status="pending_refund", note="dup verified"),
    ),
]

TECH_TRIAGE = Triage(
    intent="technical_issue",
    priority="high",
    extracted_fields=ExtractedFields(
        customer_email="marco@globex.com", order_ref=None, amount=None, product="API"
    ),
    confidence="high",
)

TECH_STEPS = [
    _step("Looking up the customer.", _call("lookup_customer", email="marco@globex.com")),
    _step("Searching the KB.", _call("search_kb", query="api latency 500 errors")),
    _step("Routing to the tech queue.", _call("route_ticket", ticket_id="T-1050", queue="tech")),
]

# -- Split 09 scenarios ------------------------------------------------------

_REPLY_BODY = (
    "Hi Jane — we've confirmed the duplicate charge on order A-4471 and will "
    "refund it in full within 5-7 business days."
)

# Multi-pending: ONE turn proposes BOTH update_ticket AND send_reply → under strict the gate
# pauses both, exercising the turn-granular batch (R2).
MULTI_STEPS = [
    _step("Looking up the customer.", _call("lookup_customer", email="jane@acme.com")),
    _step("Checking refund policy.", _call("search_kb", query="duplicate charge refund policy")),
    _step("Drafting a cited reply.", _call("draft_reply", body=_REPLY_BODY, citations=["kb-refund-001"])),
    _step(
        "Proposing a status update and a customer reply for your approval.",
        _call("update_ticket", ticket_id="T-1042", status="pending_refund", note="dup verified"),
        _call("send_reply", to="jane@acme.com", body=_REPLY_BODY, citations=["kb-refund-001"]),
    ),
]

# send_reply-only: the irreversible variant (R3) — one pending send_reply with body + citations.
SEND_REPLY_STEPS = [
    _step("Looking up the customer.", _call("lookup_customer", email="jane@acme.com")),
    _step("Checking refund policy.", _call("search_kb", query="duplicate charge refund policy")),
    _step("Drafting a cited reply.", _call("draft_reply", body=_REPLY_BODY, citations=["kb-refund-001"])),
    _step(
        "Sending the cited reply to the customer.",
        _call("send_reply", to="jane@acme.com", body=_REPLY_BODY, citations=["kb-refund-001"]),
    ),
]

# Ambiguous: the loop ends by escalating (auto under the default policy) — no pending write (§9).
AMBIGUOUS_TRIAGE = Triage(
    intent="account_access",
    priority="normal",
    extracted_fields=ExtractedFields(
        customer_email="sam@initech.com", order_ref=None, amount=None, product=None
    ),
    confidence="low",
)
AMBIGUOUS_STEPS = [
    _step("Looking up the customer.", _call("lookup_customer", email="sam@initech.com")),
    _step("Searching the KB.", _call("search_kb", query="account issue triage")),
    _step(
        "Unclear ask — escalating for a human.",
        _call("escalate", ticket_id="T-1055", level="tier2", rationale="ambiguous request; needs a human"),
    ),
]

# Spam: triage classifies spam; the model takes no action (no tool call) → done, 0 writes (§9).
SPAM_TRIAGE = Triage(
    intent="spam",
    priority="low",
    extracted_fields=ExtractedFields(
        customer_email=None, order_ref=None, amount=None, product=None
    ),
    confidence="high",
)
SPAM_STEPS = [_step("This looks like spam — no action warranted.")]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) billing — awaiting approval (the money moment: update_ticket paused under strict)
    store = RunStore(base_dir=str(OUT / "_runs_billing"))
    client = _client(store, _stub(BILLING_TRIAGE, BILLING_STEPS))
    handle = client.post(
        "/handle", json={"ticket": "billing demo", "provider": "anthropic", "policy": "strict"}
    ).json()
    _write("billing_awaiting.json", handle)

    # 2) billing — approved (resume the same run; the write fires)
    decisions = [{"approval_id": p["id"], "decision": "allow"} for p in handle["actions_pending"]]
    approved = client.post(
        "/approve", json={"run_id": handle["run_id"], "decisions": decisions}
    ).json()
    _write("billing_approved.json", approved)

    # 3) billing — rejected (fresh run; the write is recorded but never executed)
    store2 = RunStore(base_dir=str(OUT / "_runs_reject"))
    client2 = _client(store2, _stub(BILLING_TRIAGE, BILLING_STEPS))
    handle2 = client2.post(
        "/handle", json={"ticket": "billing demo", "provider": "anthropic", "policy": "strict"}
    ).json()
    rej = [{"approval_id": p["id"], "decision": "reject"} for p in handle2["actions_pending"]]
    rejected = client2.post(
        "/approve", json={"run_id": handle2["run_id"], "decisions": rej}
    ).json()
    _write("billing_rejected.json", rejected)

    # 4) tech — auto-route (route_ticket auto-approved under the default policy; no pause)
    store3 = RunStore(base_dir=str(OUT / "_runs_tech"))
    client3 = _client(store3, _stub(TECH_TRIAGE, TECH_STEPS))
    tech = client3.post(
        "/handle", json={"ticket": "tech demo", "provider": "anthropic", "policy": "default"}
    ).json()
    _write("tech_auto.json", tech)

    # 5) multi-pending — one turn proposes update_ticket + send_reply, both paused under strict (R2)
    store4 = RunStore(base_dir=str(OUT / "_runs_multi"))
    client4 = _client(store4, _stub(BILLING_TRIAGE, MULTI_STEPS))
    multi = client4.post(
        "/handle", json={"ticket": "multi demo", "provider": "anthropic", "policy": "strict"}
    ).json()
    _write("multi_pending.json", multi)

    # 6) send_reply — the irreversible variant: one pending send_reply with body + citations (R3)
    store5 = RunStore(base_dir=str(OUT / "_runs_sendreply"))
    client5 = _client(store5, _stub(BILLING_TRIAGE, SEND_REPLY_STEPS))
    sr = client5.post(
        "/handle", json={"ticket": "send reply demo", "provider": "anthropic", "policy": "strict"}
    ).json()
    _write("send_reply_pending.json", sr)

    # 7) ambiguous — escalate auto under default; no pending write (§9 ambiguous)
    store6 = RunStore(base_dir=str(OUT / "_runs_ambiguous"))
    client6 = _client(store6, _stub(AMBIGUOUS_TRIAGE, AMBIGUOUS_STEPS))
    amb = client6.post(
        "/handle", json={"ticket": "ambiguous demo", "provider": "anthropic", "policy": "default"}
    ).json()
    _write("ambiguous_escalate.json", amb)

    # 8) spam — triage=spam, no action taken (§9 spam)
    store7 = RunStore(base_dir=str(OUT / "_runs_spam"))
    client7 = _client(store7, _stub(SPAM_TRIAGE, SPAM_STEPS))
    spam = client7.post(
        "/handle", json={"ticket": "spam demo", "provider": "anthropic", "policy": "default"}
    ).json()
    _write("spam_noaction.json", spam)

    # 9) meta — config + examples (drives the config sheet / example buttons)
    _write("config.json", client.get("/config").json())
    _write("examples.json", client.get("/examples").json())
    _write("health.json", client.get("/health").json())


def _write(name: str, data: object) -> None:
    path = OUT / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(OUT.parent)}")


if __name__ == "__main__":
    main()
