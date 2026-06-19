"""T4 — per-run DB isolation under the bounded pool (Split 06 R3, spec §11/§13, E4).

The harness gives each run its own seeded DB; a shared connection under the ThreadPoolExecutor
would race and silently corrupt the numbers. Two checks: (1) >6 concurrent runs that each
auto-execute a *distinct* write read back only their own mutation (no cross-run bleed), and (2)
the real must_gate suite, run concurrently, keeps the invariant for every run and leaves each DB
holding only its own run row.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from eval.run import run_suite
from eval.scenario import load_scenarios
from relay import handle
from relay.agent import _run_db_path
from relay.backend import db
from relay.provider.base import ModelStep, NormalizedToolCall, Usage
from relay.provider.stub import StubProvider

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def _triage():
    return {
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


def _auto_write_stub(status: str) -> StubProvider:
    from relay.models import Triage

    step = ModelStep(
        text="writing",
        tool_calls=[
            NormalizedToolCall(
                id="tu_1", name="update_ticket", args={"ticket_id": "T-1042", "status": status}
            )
        ],
        usage=Usage(input_tokens=10, output_tokens=2),
        stop_reason="tool_use",
    )
    return StubProvider(triage_result=Triage.model_validate(_triage()), steps=[step])


def test_concurrent_distinct_writes_do_not_bleed(tmp_path: Path) -> None:
    n = 8  # > the pool size (6) so tasks genuinely queue + overlap
    store = str(tmp_path)
    statuses = [f"st_{i}" for i in range(n)]

    def _go(i: int) -> str:
        rid = f"iso_{i}"
        # policy=auto → the write fires immediately (still audited decision="auto"), so each run
        # mutates its OWN seeded DB. If DBs bled, we'd read another run's status back.
        handle(
            "charged twice",
            provider="stub",
            policy="auto",
            run_id=rid,
            store_dir=store,
            _provider=_auto_write_stub(statuses[i]),
        )
        return rid

    with ThreadPoolExecutor(max_workers=6) as pool:
        rids = list(pool.map(_go, range(n)))

    for i, rid in enumerate(rids):
        conn = db.connect(_run_db_path(rid, store))
        try:
            assert db.get_ticket(conn, "T-1042")["status"] == statuses[i]  # only its own write
            # exactly one run row — this DB is private to this run.
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
            assert conn.execute("SELECT id FROM runs").fetchone()["id"] == rid
        finally:
            conn.close()

    # A control fresh seed is untouched by any of the runs.
    control = db.reset_to_seed()
    assert db.get_ticket(control, "T-1042")["status"] == "open"
    control.close()


def test_must_gate_suite_runs_isolated_and_invariant_holds(tmp_path: Path) -> None:
    scenarios = [s for s in load_scenarios(SCENARIOS_DIR) if s.split == "must_gate"]
    assert len(scenarios) > 6  # genuinely exercises the pool
    records = run_suite(scenarios, distributional_providers=[], repeats=1, store_dir=str(tmp_path))

    assert all(r.metrics.no_unapproved_writes for r in records)
    for r in records:
        conn = db.connect(_run_db_path(r.run_id, str(tmp_path)))
        try:
            # Each run DB holds exactly one run row (its own); tickets untouched (writes paused).
            ids = [row["id"] for row in conn.execute("SELECT id FROM runs").fetchall()]
            assert ids == [r.run_id]
            assert db.get_ticket(conn, "T-1042")["status"] == "open"
        finally:
            conn.close()
