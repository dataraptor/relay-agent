"""T2 + E3 — backend ops round-trip and per-run isolation under concurrency."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from relay.backend import db


def test_seed_round_trip_reads_back_seeded_values() -> None:
    conn = db.reset_to_seed()
    jane = db.get_customer(conn, email="jane@acme.com")
    assert jane is not None
    assert jane["plan"] == "Pro"
    assert jane["status"] == "active"
    assert json.loads(jane["flags_json"])["double_charge_detected"] is True

    # Same customer is reachable by id.
    assert db.get_customer(conn, customer_id="C-001")["email"] == "jane@acme.com"

    ticket = db.get_ticket(conn, "T-1042")
    assert ticket is not None
    assert ticket["customer_id"] == "C-001"
    assert ticket["status"] == "open"
    assert ticket["queue"] == "unassigned"


def test_get_customer_requires_a_selector() -> None:
    conn = db.reset_to_seed()
    with pytest.raises(ValueError):
        db.get_customer(conn)


def test_get_missing_returns_none() -> None:
    conn = db.reset_to_seed()
    assert db.get_customer(conn, email="nobody@nowhere.com") is None
    assert db.get_ticket(conn, "T-9999") is None


def test_update_ticket_mutates_and_reread_reflects_it() -> None:
    conn = db.reset_to_seed()
    out = db.update_ticket(
        conn, "T-1042", status="pending_refund", fields={"queue": "billing"}, note="dup verified"
    )
    assert out["status"] == "pending_refund"
    assert out["queue"] == "billing"
    # A fresh read reflects the mutation.
    assert db.get_ticket(conn, "T-1042")["status"] == "pending_refund"


def test_update_ticket_ignores_non_whitelisted_fields() -> None:
    conn = db.reset_to_seed()
    before = db.get_ticket(conn, "T-1042")
    db.update_ticket(conn, "T-1042", fields={"customer_id": "C-999", "id": "HACK"})
    after = db.get_ticket(conn, "T-1042")
    assert after["customer_id"] == before["customer_id"]
    assert db.get_ticket(conn, "HACK") is None


def test_update_ticket_missing_raises() -> None:
    conn = db.reset_to_seed()
    with pytest.raises(KeyError):
        db.update_ticket(conn, "T-9999", status="closed")


def test_route_ticket_sets_queue() -> None:
    conn = db.reset_to_seed()
    out = db.route_ticket(conn, "T-1050", "tech")
    assert out["queue"] == "tech"
    assert db.get_ticket(conn, "T-1050")["queue"] == "tech"


def test_escalate_flips_status() -> None:
    conn = db.reset_to_seed()
    out = db.escalate(conn, "T-1050", level="urgent", rationale="paying customer blocked")
    assert out["status"] == "escalated"


def test_ledger_inserts_round_trip() -> None:
    conn = db.reset_to_seed()
    run_id = db.insert_run(
        conn,
        id="run-1",
        ticket_id="T-1042",
        provider="anthropic",
        model="claude-sonnet-4-6",
        prompt_version="relay-prompts-v1",
    )
    assert run_id == "run-1"
    db.update_run(conn, "run-1", status="awaiting_approval", step=4, messages_json="[]")
    row = conn.execute(
        "SELECT status, step, messages_json FROM runs WHERE id=?", ("run-1",)
    ).fetchone()
    assert row["status"] == "awaiting_approval"
    assert row["step"] == 4
    assert row["messages_json"] == "[]"

    llm_id = db.insert_llm_call(
        conn,
        run_id="run-1",
        ticket_id="T-1042",
        kind="triage",
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=0.0042,
    )
    assert llm_id >= 1

    action_id = db.insert_action_log(
        conn,
        ticket_id="T-1042",
        run_id="run-1",
        tool="update_ticket",
        decision="approved",
        proposed_args_json=json.dumps({"status": "pending_refund"}),
        approver="operator",
    )
    assert action_id >= 1

    tool_id = db.insert_tool_call(
        conn,
        run_id="run-1",
        ticket_id="T-1042",
        step=4,
        tool="update_ticket",
        args_json=json.dumps({"status": "pending_refund"}),
    )
    assert tool_id >= 1

    # tool_calls has no token columns (cost lives in llm_calls, §11).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
    assert "input_tokens" not in cols and "cost_usd" not in cols


def test_update_run_missing_raises() -> None:
    conn = db.reset_to_seed()
    with pytest.raises(KeyError):
        db.update_run(conn, "no-such-run", status="done")


def test_update_run_partial_fields_only() -> None:
    # status=None path: update just the step without touching status/messages_json.
    conn = db.reset_to_seed()
    db.insert_run(
        conn,
        id="run-2",
        ticket_id=None,
        provider="anthropic",
        model="claude-sonnet-4-6",
        prompt_version="relay-prompts-v1",
        status="running",
    )
    db.update_run(conn, "run-2", step=2)
    row = conn.execute("SELECT status, step FROM runs WHERE id=?", ("run-2",)).fetchone()
    assert row["status"] == "running"  # unchanged
    assert row["step"] == 2


def test_schema_has_all_seven_tables() -> None:
    conn = db.connect()
    db.init_schema(conn)
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "customers",
        "tickets",
        "kb_chunks",
        "runs",
        "llm_calls",
        "actions_log",
        "tool_calls",
    } <= names


def _mutate_isolated(status: str) -> str:
    """Each call gets its OWN fresh seeded DB, mutates T-1042, and reads it back."""
    conn = db.reset_to_seed()
    db.update_ticket(conn, "T-1042", status=status)
    return db.get_ticket(conn, "T-1042")["status"]


def test_reset_to_seed_is_isolated_no_cross_db_bleed() -> None:
    # E3: open N>=4 DBs in parallel threads, mutate each differently, assert no bleed.
    statuses = [f"status_{i}" for i in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_mutate_isolated, statuses))
    # Each DB read back exactly its own write.
    assert results == statuses

    # A separately seeded DB is untouched by any of the above.
    clean = db.reset_to_seed()
    assert db.get_ticket(clean, "T-1042")["status"] == "open"


def test_two_connections_do_not_share_memory_state() -> None:
    a = db.reset_to_seed()
    b = db.reset_to_seed()
    db.update_ticket(a, "T-1042", status="closed")
    # b is a different :memory: DB — it must not see a's mutation.
    assert db.get_ticket(b, "T-1042")["status"] == "open"
