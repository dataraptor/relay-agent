"""Per-run isolated SQLite backend: connection, schema, CRUD ops, ledger inserts (§11).

**Isolation is the load-bearing property (E3).** Every :func:`connect` returns an independent
connection; the default ``:memory:`` target is a brand-new empty database each call, so the
parallel eval pool (Split 06) never races on shared state. There is **no module-level shared
connection** — a singleton here would be a correctness bug.

Mutating ops (``update_ticket``/``route_ticket``/``escalate``) are plain DB writes. The gate
that decides *whether* a write runs lives in Split 03 — it is deliberately not here.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

#: Columns a caller may set via ``update_ticket(..., fields=...)``.
_TICKET_UPDATABLE = ("status", "queue", "priority", "intent", "subject")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open an **independent** SQLite connection (``check_same_thread=False``).

    ``path=None`` (default) opens a fresh in-memory DB unique to this call — the isolation
    primitive the eval pool relies on. A file path opens/creates that file.
    """
    conn = sqlite3.connect(path or ":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply ``schema.sql`` (all seven tables) to ``conn``."""
    sql = files("relay.backend").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def reset_to_seed(path: str | None = None) -> sqlite3.Connection:
    """Return a **fresh seeded** database (a new per-run DB), per §11.

    "Reset to seed" means a new isolated DB, *not* mutating one shared singleton.
    """
    from .seed import seed  # local import avoids a db<->seed import cycle

    conn = connect(path)
    init_schema(conn)
    seed(conn)
    return conn


# --- Reads ------------------------------------------------------------------


def get_customer(
    conn: sqlite3.Connection,
    email: str | None = None,
    customer_id: str | None = None,
) -> dict[str, Any] | None:
    """Fetch a customer by ``email`` or ``customer_id`` (at least one required)."""
    if email is not None:
        cur = conn.execute("SELECT * FROM customers WHERE email = ?", (email,))
    elif customer_id is not None:
        cur = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,))
    else:
        raise ValueError("get_customer requires email or customer_id")
    return _row_to_dict(cur.fetchone())


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    return _row_to_dict(cur.fetchone())


# --- Ticket mutations (plain writes; gating is Split 03) ---------------------


def update_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    status: str | None = None,
    fields: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Mutate a ticket row. ``status`` plus any whitelisted ``fields`` keys are applied.

    ``note`` is accepted for signature parity with the tool (§7) but is *not* a ``tickets``
    column — it belongs in the ``actions_log`` audit row, not on the ticket.
    """
    updates: dict[str, Any] = {}
    if status is not None:
        updates["status"] = status
    for key, value in (fields or {}).items():
        if key in _TICKET_UPDATABLE:
            updates[key] = value
    updates["updated_at"] = _now()

    assignments = ", ".join(f"{col} = ?" for col in updates)
    params = [*updates.values(), ticket_id]
    cur = conn.execute(f"UPDATE tickets SET {assignments} WHERE id = ?", params)
    if cur.rowcount == 0:
        raise KeyError(f"ticket {ticket_id!r} not found")
    conn.commit()
    ticket = get_ticket(conn, ticket_id)
    assert ticket is not None  # just updated it
    return ticket


def route_ticket(conn: sqlite3.Connection, ticket_id: str, queue: str) -> dict[str, Any]:
    return update_ticket(conn, ticket_id, fields={"queue": queue})


def escalate(
    conn: sqlite3.Connection, ticket_id: str, level: str, rationale: str
) -> dict[str, Any]:
    """Escalate a ticket. ``level``/``rationale`` are logged in ``actions_log`` (Split 03);
    on the ticket itself we just flip the status to ``escalated``."""
    return update_ticket(conn, ticket_id, status="escalated")


# --- Ledger / run-state inserts (written from Split 03 onward) ---------------


def insert_run(
    conn: sqlite3.Connection,
    *,
    id: str,
    ticket_id: str | None,
    provider: str,
    model: str,
    prompt_version: str,
    status: str = "running",
    step: int = 0,
    messages_json: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> str:
    ts = created_at or _now()
    conn.execute(
        "INSERT INTO runs (id, ticket_id, provider, model, prompt_version, status, step, "
        "messages_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            id,
            ticket_id,
            provider,
            model,
            prompt_version,
            status,
            step,
            messages_json,
            ts,
            updated_at or ts,
        ),
    )
    conn.commit()
    return id


def update_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str | None = None,
    step: int | None = None,
    messages_json: str | None = None,
) -> None:
    updates: dict[str, Any] = {}
    if status is not None:
        updates["status"] = status
    if step is not None:
        updates["step"] = step
    if messages_json is not None:
        updates["messages_json"] = messages_json
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{col} = ?" for col in updates)
    params = [*updates.values(), run_id]
    cur = conn.execute(f"UPDATE runs SET {assignments} WHERE id = ?", params)
    if cur.rowcount == 0:
        raise KeyError(f"run {run_id!r} not found")
    conn.commit()


def insert_llm_call(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ticket_id: str | None,
    kind: str,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    latency_ms: int | None = None,
    cost_usd: float = 0.0,
    ts: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO llm_calls (run_id, ticket_id, kind, provider, model, input_tokens, "
        "output_tokens, cache_read_tokens, cache_creation_tokens, latency_ms, cost_usd, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            ticket_id,
            kind,
            provider,
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_creation_tokens,
            latency_ms,
            cost_usd,
            ts or _now(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def insert_action_log(
    conn: sqlite3.Connection,
    *,
    ticket_id: str | None,
    run_id: str,
    tool: str,
    decision: str,
    proposed_args_json: str | None = None,
    final_args_json: str | None = None,
    approver: str | None = None,
    result_json: str | None = None,
    ts: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO actions_log (ticket_id, run_id, tool, proposed_args_json, "
        "final_args_json, decision, approver, result_json, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticket_id,
            run_id,
            tool,
            proposed_args_json,
            final_args_json,
            decision,
            approver,
            result_json,
            ts or _now(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def insert_tool_call(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ticket_id: str | None,
    step: int,
    tool: str,
    args_json: str | None = None,
    result_json: str | None = None,
    latency_ms: int | None = None,
    ts: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO tool_calls (run_id, ticket_id, step, tool, args_json, result_json, "
        "latency_ms, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, ticket_id, step, tool, args_json, result_json, latency_ms, ts or _now()),
    )
    conn.commit()
    return int(cur.lastrowid)
