"""T5 — seed integrity: counts in range, every kb_chunk grounded, worked-example targets exist."""

from __future__ import annotations

import json

from relay.backend import db


def test_customer_count_about_eight() -> None:
    conn = db.reset_to_seed()
    n = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    assert n == 8


def test_kb_chunk_count_in_range_12_to_15() -> None:
    conn = db.reset_to_seed()
    n = conn.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]
    assert 12 <= n <= 15


def test_every_kb_chunk_has_source_url_text() -> None:
    conn = db.reset_to_seed()
    rows = conn.execute("SELECT source, url, section, text FROM kb_chunks").fetchall()
    for r in rows:
        assert r["source"] and r["url"] and r["text"]
        assert r["url"].startswith("http")


def test_plans_span_free_pro_enterprise() -> None:
    conn = db.reset_to_seed()
    plans = {r[0] for r in conn.execute("SELECT DISTINCT plan FROM customers").fetchall()}
    assert {"Free", "Pro", "Enterprise"} <= plans


def test_required_flags_present_across_customers() -> None:
    conn = db.reset_to_seed()
    flags_seen: set[str] = set()
    for (flags_json,) in conn.execute("SELECT flags_json FROM customers").fetchall():
        flags_seen.update(k for k, v in json.loads(flags_json).items() if v)
    assert {"double_charge_detected", "past_due", "abuse_flag"} <= flags_seen


def test_at_least_one_double_charge_customer() -> None:
    conn = db.reset_to_seed()
    rows = conn.execute("SELECT flags_json FROM customers").fetchall()
    assert any(json.loads(r[0]).get("double_charge_detected") for r in rows)


def test_billing_dispute_target_ticket_exists() -> None:
    # Split 03's money demo needs a concrete target: jane@acme.com / double-charge / T-1042.
    conn = db.reset_to_seed()
    ticket = db.get_ticket(conn, "T-1042")
    assert ticket is not None
    customer = db.get_customer(conn, customer_id=ticket["customer_id"])
    assert customer["email"] == "jane@acme.com"
    assert json.loads(customer["flags_json"]).get("double_charge_detected") is True


def test_duplicate_charge_grounding_chunk_exists() -> None:
    conn = db.reset_to_seed()
    texts = [r[0] for r in conn.execute("SELECT text FROM kb_chunks").fetchall()]
    assert any("Duplicate charges are refunded in full" in t for t in texts)


def test_some_open_tickets_exist_for_writes() -> None:
    conn = db.reset_to_seed()
    n_open = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0]
    assert n_open >= 3
