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

    # 5) meta — config + examples (drives the config sheet / example buttons)
    _write("config.json", client.get("/config").json())
    _write("examples.json", client.get("/examples").json())
    _write("health.json", client.get("/health").json())


def _write(name: str, data: object) -> None:
    path = OUT / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(OUT.parent)}")


if __name__ == "__main__":
    main()
