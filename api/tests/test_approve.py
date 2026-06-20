"""T3 — turn-granular batch over HTTP · T4 — run-store isolation + reload (Tier-1, no key)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import call, make_stub, step
from relay_api.runs import RunStore


def _two_write_stub():
    """A turn that proposes TWO writes at once (multi-pending gate, §8)."""
    steps = [
        step("Looking up the customer.", call("lookup_customer", email="jane@acme.com")),
        step(
            "Proposing two writes for your approval.",
            call("update_ticket", ticket_id="T-1042", status="pending_refund"),
            call("escalate", ticket_id="T-1042", level="tier_2", rationale="dup charge"),
        ),
    ]
    return make_stub(steps)


def test_t3_batch_allow_one_reject_one(make_client) -> None:
    client = make_client(_two_write_stub())
    handle = client.post("/handle", json={"ticket": "charged twice", "policy": "strict"}).json()
    assert handle["status"] == "awaiting_approval"
    pending = handle["actions_pending"]
    assert len(pending) == 2

    by_tool = {p["tool"]: p["id"] for p in pending}
    decisions = [
        {"approval_id": by_tool["update_ticket"], "decision": "allow"},
        {"approval_id": by_tool["escalate"], "decision": "reject"},
    ]
    approve = client.post(
        "/approve", json={"run_id": handle["run_id"], "decisions": decisions}
    ).json()
    assert approve["status"] == "done"
    taken = {a["tool"]: a["decision"] for a in approve["actions_taken"]}
    assert taken.get("update_ticket") == "approved"
    assert "escalate" not in taken  # rejected → not in actions_taken
    rejected = [s["tool"] for s in approve["trace"] if s["state"] == "rejected"]
    assert "escalate" in rejected


def test_t3_decisions_missing_one_pending_is_400(make_client) -> None:
    """A ``decisions`` array that does not cover every pending action → ``400 bad_request``."""
    client = make_client(_two_write_stub())
    handle = client.post("/handle", json={"ticket": "charged twice", "policy": "strict"}).json()
    only_one = handle["actions_pending"][0]["id"]
    resp = client.post(
        "/approve",
        json={
            "run_id": handle["run_id"],
            "decisions": [{"approval_id": only_one, "decision": "allow"}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "bad_request"


def test_t4_two_runs_do_not_bleed(make_client) -> None:
    """Two concurrent-ish runs each see only their own state; reload by id is independent."""
    c1 = make_client(make_stub())
    c2 = make_client(make_stub())
    h1 = c1.post("/handle", json={"ticket": "first", "policy": "strict"}).json()
    h2 = c2.post("/handle", json={"ticket": "second", "policy": "strict"}).json()
    assert h1["run_id"] != h2["run_id"]

    # Approve run 1; run 2 stays suspended and is unaffected.
    a1 = c1.post(
        "/approve",
        json={
            "run_id": h1["run_id"],
            "decisions": [{"approval_id": h1["actions_pending"][0]["id"], "decision": "allow"}],
        },
    ).json()
    assert a1["status"] == "done"

    # Run 2 reloads its OWN suspended transcript and fires its own write.
    a2 = c2.post(
        "/approve",
        json={
            "run_id": h2["run_id"],
            "decisions": [{"approval_id": h2["actions_pending"][0]["id"], "decision": "allow"}],
        },
    ).json()
    assert a2["status"] == "done"
    assert a2["id"] == h2["run_id"]


def test_t4_unknown_run_id_is_404(client: TestClient) -> None:
    resp = client.post(
        "/approve",
        json={
            "run_id": "run_does_not_exist",
            "decisions": [{"approval_id": "x", "decision": "allow"}],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "run_not_found"


def test_t4_db_file_lands_in_run_store(client: TestClient, store: RunStore) -> None:
    """The run's durable file DB lives at the store's per-run path (so /approve can reload it)."""
    import os

    handle = client.post("/handle", json={"ticket": "charged twice", "policy": "strict"}).json()
    assert os.path.exists(store.db_path(handle["run_id"]))
