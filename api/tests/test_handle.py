"""T1 — ``/handle`` happy path · T7 — RunView fidelity (Tier-1, no key)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from relay.gate import STATE_CHANGE_TOOLS
from relay_api.runs import RunStore


def test_t1_handle_suspends_without_firing_a_write(client: TestClient, store: RunStore) -> None:
    """A ticket that proposes a gated write returns ``awaiting_approval`` with a populated trace,
    a pending action carrying args + rationale, a proposed records diff, and a cost block — and
    **no state-change ``tool_calls`` write row exists yet** (the gate held, across HTTP)."""
    resp = client.post("/handle", json={"ticket": "charged twice", "policy": "strict"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "awaiting_approval"
    assert body["triage"]["intent"] == "billing_dispute"

    # trace: 2 reads executed + a draft (read_class) + an awaiting write
    by_tool = [(s["tool"], s["cls"], s["state"]) for s in body["trace"]]
    assert ("lookup_customer", "read", "executed") in by_tool
    assert ("search_kb", "read", "executed") in by_tool
    assert ("draft_reply", "read_class", "executed") in by_tool
    assert ("update_ticket", "state_change", "awaiting") in by_tool

    # pending action: args + rationale present
    assert len(body["actions_pending"]) == 1
    pending = body["actions_pending"][0]
    assert pending["tool"] == "update_ticket"
    assert pending["args"]["status"] == "pending_refund"
    assert "approval" in pending["rationale"].lower() or pending["rationale"]

    # records: proposed diff open -> pending_refund
    assert body["records"]["proposed"] == {
        "field": "status",
        "current": "open",
        "proposed": "pending_refund",
    }
    assert body["records"]["customer"]["email"] == "jane@acme.com"

    # cost block sourced from llm_calls
    assert body["cost"]["total_usd"] > 0
    kinds = [c["kind"] for c in body["cost"]["by_call"]]
    assert "triage" in kinds and "faithfulness" in kinds

    # THE assertion: no state-change write row was persisted on /handle.
    conn = store.open_db(body["run_id"])
    try:
        written = [
            r["tool"]
            for r in conn.execute(
                "SELECT tool FROM tool_calls WHERE run_id = ?", (body["run_id"],)
            ).fetchall()
            if r["tool"] in STATE_CHANGE_TOOLS
        ]
    finally:
        conn.close()
    assert written == []


def test_t7_runview_fidelity(client: TestClient) -> None:
    """Field names align with §11/the Outcome contract; the draft sub-block carries
    body/citations/faithfulness; ``cost.by_call`` sums to ``cost.total_usd``."""
    body = client.post("/handle", json={"ticket": "charged twice", "policy": "strict"}).json()

    # §11 field-name parity (RunView is a superset of Outcome — never a rename).
    for field in (
        "id",
        "run_id",
        "ticket_id",
        "triage",
        "status",
        "actions_pending",
        "actions_taken",
        "provider",
        "model",
        "prompt_version",
        "n_runs",
    ):
        assert field in body
    assert body["id"] == body["run_id"]
    assert body["provider"] == "anthropic"
    assert body["model"] == "claude-sonnet-4-6"

    # the draft-reply trace step carries the reply sub-block
    draft_step = next(s for s in body["trace"] if s["tool"] == "draft_reply")
    draft = draft_step["draft"]
    assert draft["body"]
    assert draft["citations"][0]["chunk_id"] == "kb-refund-001"
    assert draft["citations"][0]["text"]  # enriched with the cited chunk text
    assert draft["faithfulness"] is not None

    # cost.by_call sums to total_usd (auditable, §13)
    cost = body["cost"]
    assert abs(sum(c["cost_usd"] for c in cost["by_call"]) - cost["total_usd"]) < 1e-9
    assert cost["tokens"]["input"] > 0


def test_handle_auto_policy_fires_no_pending(make_client) -> None:
    """``policy=auto`` auto-executes the write (audited ``auto``) — done in one request, the write
    is in ``actions_taken`` with decision ``auto`` and the trace step is ``executed``."""
    from conftest import make_stub

    client = make_client(make_stub())
    body = client.post("/handle", json={"ticket": "charged twice", "policy": "auto"}).json()
    assert body["status"] == "done"
    taken = [(a["tool"], a["decision"]) for a in body["actions_taken"]]
    assert ("update_ticket", "auto") in taken
    update_step = next(s for s in body["trace"] if s["tool"] == "update_ticket")
    assert update_step["state"] == "executed" and update_step["decision"] == "auto"
    assert body["records"]["ticket"]["status"] == "pending_refund"
