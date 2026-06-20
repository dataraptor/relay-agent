"""Projection coverage across every tool + records/cost edge cases (Tier-1, no key).

These exercise the RunView projection (``runs.py``) for the auto-route/escalate/send_reply tools,
the proposed-queue diff, the blocked (deny) branch, the records-empty case, and the run-store
cleanup — the parts the canonical billing demo doesn't reach.
"""

from __future__ import annotations

import relay
from conftest import call, make_stub, step
from relay_api.runs import RunStore, project_run_view


def test_route_ticket_auto_and_records_queue(make_client) -> None:
    """default policy auto-executes route_ticket → executed step, committed queue in records."""
    steps = [
        step("Looking up the customer.", call("lookup_customer", email="marco@globex.com")),
        step("Routing to tech.", call("route_ticket", ticket_id="T-1050", queue="tech")),
    ]
    client = make_client(make_stub(steps))
    body = client.post("/handle", json={"ticket": "api errors", "policy": "default"}).json()
    assert body["status"] == "done"
    route_step = next(s for s in body["trace"] if s["tool"] == "route_ticket")
    assert route_step["state"] == "executed" and route_step["decision"] == "auto"
    assert "queue=tech" in route_step["result_summary"]
    assert body["records"]["ticket"]["queue"] == "tech"


def test_route_ticket_proposed_queue_diff(make_client) -> None:
    """strict policy pauses route_ticket → records.proposed shows the queue diff."""
    steps = [
        step("Looking up the customer.", call("lookup_customer", email="marco@globex.com")),
        step("Proposing a route.", call("route_ticket", ticket_id="T-1050", queue="tech")),
    ]
    client = make_client(make_stub(steps))
    body = client.post("/handle", json={"ticket": "api errors", "policy": "strict"}).json()
    assert body["status"] == "awaiting_approval"
    assert body["records"]["proposed"] == {
        "field": "queue",
        "current": "unassigned",
        "proposed": "tech",
    }


def test_escalate_auto_summary(make_client) -> None:
    steps = [
        step("Looking up the customer.", call("lookup_customer", email="sam@initech.com")),
        step(
            "Escalating; low confidence.",
            call("escalate", ticket_id="T-1055", level="tier_2", rationale="ambiguous"),
        ),
    ]
    client = make_client(make_stub(steps))
    body = client.post(
        "/handle", json={"ticket": "something feels off", "policy": "default"}
    ).json()
    assert body["status"] == "done"
    esc = next(s for s in body["trace"] if s["tool"] == "escalate")
    assert esc["decision"] == "auto"
    assert "escalated" in esc["result_summary"]


def test_send_reply_executed_and_ticket_from_recent(make_client) -> None:
    """Approving a send_reply executes it (summary 'sent …'); with no ticket_id arg the records
    ticket falls back to the customer's most-recent ticket from the lookup result."""
    steps = [
        step("Looking up the customer.", call("lookup_customer", email="jane@acme.com")),
        step(
            "Proposing to send the reply.",
            call("send_reply", to="jane@acme.com", body="Refund issued.", citations=[]),
        ),
    ]
    client = make_client(make_stub(steps))
    handle = client.post("/handle", json={"ticket": "refund", "policy": "strict"}).json()
    assert handle["status"] == "awaiting_approval"
    # ticket sourced from recent_tickets (send_reply carries no ticket_id)
    assert handle["records"]["ticket"]["id"] == "T-1042"

    pid = handle["actions_pending"][0]["id"]
    approve = client.post(
        "/approve",
        json={"run_id": handle["run_id"], "decisions": [{"approval_id": pid, "decision": "allow"}]},
    ).json()
    sent = next(s for s in approve["trace"] if s["tool"] == "send_reply")
    assert sent["state"] == "executed" and sent["result_summary"].startswith("sent")


def test_records_none_when_no_customer_or_ticket(make_client) -> None:
    """A run that only searches the KB touches no customer/ticket → records is null."""
    steps = [step("Just checking policy.", call("search_kb", query="refund policy"))]
    client = make_client(make_stub(steps))
    body = client.post("/handle", json={"ticket": "policy question", "policy": "default"}).json()
    assert body["status"] == "done"
    assert body["records"] is None


def test_blocked_branch_via_deny_policy(store: RunStore) -> None:
    """The projection renders a ``blocked`` (policy=deny) action — exercised by driving the engine
    directly with a per-tool deny (the HTTP surface only exposes presets; Split 09 may add deny)."""
    triage = relay.Triage.model_validate(
        {
            "intent": "abuse_report",
            "priority": "high",
            "extracted_fields": {
                "customer_email": None,
                "order_ref": None,
                "amount": None,
                "product": None,
            },
            "confidence": "low",
        }
    )
    steps = [
        step("Escalating.", call("escalate", ticket_id="T-1042", level="tier_2", rationale="x")),
    ]
    stub = make_stub(steps, triage=triage)
    outcome = relay.handle(
        "abuse",
        provider="anthropic",
        policy={"escalate": "deny"},
        run_id="blocked_run",
        store_dir=store.base_dir,
        _provider=stub,
    )
    conn = store.open_db("blocked_run")
    try:
        view = project_run_view(outcome, conn)
    finally:
        conn.close()
    blocked = [s for s in view.trace if s.state == "blocked"]
    assert blocked and blocked[0].tool == "escalate"
    assert blocked[0].decision == "blocked"


def test_run_store_eviction(tmp_path) -> None:
    """The run store caps retained runs and deletes evicted file DBs (best-effort cleanup, R2)."""
    import os

    store = RunStore(base_dir=str(tmp_path / "s"), max_runs=1)
    stub1 = make_stub()
    out1 = relay.handle(
        "a",
        provider="anthropic",
        policy="auto",
        run_id="r1",
        store_dir=store.base_dir,
        _provider=stub1,
    )
    store.register("r1", stub1, "anthropic", None)
    assert os.path.exists(store.db_path("r1"))

    stub2 = make_stub()
    relay.handle(
        "b",
        provider="anthropic",
        policy="auto",
        run_id="r2",
        store_dir=store.base_dir,
        _provider=stub2,
    )
    store.register("r2", stub2, "anthropic", None)

    # r1 evicted (cap=1): registry miss + its DB file removed.
    import pytest

    from relay_api.runs import RunNotFoundError

    with pytest.raises(RunNotFoundError):
        store.get("r1")
    assert not os.path.exists(store.db_path("r1"))
    assert store.get("r2").run_id == "r2"
    assert out1.status == "done"
