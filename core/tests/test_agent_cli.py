"""CLI core commands (R6) and the turn-granular approval enforcement (E2).

The CLI builds a real provider via ``relay.agent.make_provider``; here we monkeypatch that to
return a shared StubProvider so the no-key path drives the identical money-demo flow as the
real Anthropic path (E1 with-key note in the Evaluation Report).
"""

from __future__ import annotations

import json

from relay import cli
from relay.backend import db
from relay.cost import Usage
from relay.provider import StubProvider
from relay.provider.base import ModelStep, NormalizedToolCall


def _triage_dict() -> dict:
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


def _step(text, *calls):
    from relay.models import Triage  # local: keep top-level imports tidy

    _ = Triage  # noqa: F841
    return ModelStep(
        text=text,
        tool_calls=list(calls),
        usage=Usage(input_tokens=50, output_tokens=5),
        stop_reason="tool_use" if calls else "end_turn",
    )


def _install_stub(monkeypatch, steps):
    from relay.models import Triage

    stub = StubProvider(triage_result=Triage.model_validate(_triage_dict()), steps=steps)
    monkeypatch.setattr("relay.agent.make_provider", lambda provider, model: stub)
    return stub


def test_cli_handle_pauses_then_approve_fires(monkeypatch, tmp_path, capsys) -> None:
    """E1 (no key): money demo via CLI — handle reaches a paused update_ticket; approve fires it."""
    steps = [
        _step(
            "looking up",
            NormalizedToolCall(id="tu_1", name="lookup_customer", args={"email": "jane@acme.com"}),
        ),
        _step(
            "drafting",
            NormalizedToolCall(
                id="tu_2",
                name="draft_reply",
                args={"body": "We will refund.", "citations": ["kb-refund-001"]},
            ),
        ),
        _step(
            "propose write",
            NormalizedToolCall(
                id="tu_3",
                name="update_ticket",
                args={"ticket_id": "T-1042", "status": "pending_refund"},
            ),
        ),
        _step("done — pending approval"),
    ]
    _install_stub(monkeypatch, steps)
    sd = str(tmp_path)

    rc = cli.main(["handle", "--ticket", "charged twice", "--store-dir", sd])
    out = capsys.readouterr().out
    assert rc == 0
    assert "status         : awaiting_approval" in out
    assert "PENDING APPROVAL" in out and "update_ticket" in out

    # Recover the run id and the approval id from the printed output.
    run_id = next(
        line.split(":", 1)[1].strip()
        for line in out.splitlines()
        if line.startswith("run/outcome id")
    )
    approval_id = next(
        tok.split("=", 1)[1]
        for line in out.splitlines()
        for tok in line.split()
        if tok.startswith("approval_id=")
    )

    conn = db.connect(f"{sd}/runs/{run_id}.db")
    assert db.get_ticket(conn, "T-1042")["status"] == "open"  # no write yet
    conn.close()

    rc = cli.main(
        [
            "approve",
            "--outcome",
            run_id,
            "--approval",
            approval_id,
            "--decision",
            "allow",
            "--store-dir",
            sd,
        ]
    )
    out2 = capsys.readouterr().out
    assert rc == 0
    assert "status         : done" in out2
    conn = db.connect(f"{sd}/runs/{run_id}.db")
    assert db.get_ticket(conn, "T-1042")["status"] == "pending_refund"  # fired only on approve
    conn.close()


def test_cli_two_pending_single_approval_errors_decisions_batch_succeeds(
    monkeypatch, tmp_path, capsys
) -> None:
    """E2: the single ``--approval`` form errors on a >1-pending turn; ``--decisions`` works."""
    steps = [
        _step(
            "two writes",
            NormalizedToolCall(
                id="tu_1",
                name="update_ticket",
                args={"ticket_id": "T-1042", "status": "pending_refund"},
            ),
            NormalizedToolCall(
                id="tu_2", name="route_ticket", args={"ticket_id": "T-1042", "queue": "billing"}
            ),
        ),
        _step("done"),
    ]
    _install_stub(monkeypatch, steps)
    sd = str(tmp_path)
    cli.main(["handle", "--ticket", "x", "--policy", "strict", "--store-dir", sd])
    out = capsys.readouterr().out
    run_id = next(
        line.split(":", 1)[1].strip()
        for line in out.splitlines()
        if line.startswith("run/outcome id")
    )

    # Single --approval form errors (turn has 2 pending) → exit code 2, nothing fired.
    rc = cli.main(
        [
            "approve",
            "--outcome",
            run_id,
            "--approval",
            "tu_1",
            "--decision",
            "allow",
            "--store-dir",
            sd,
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "2 pending actions" in err
    conn = db.connect(f"{sd}/runs/{run_id}.db")
    assert db.get_ticket(conn, "T-1042")["status"] == "open"
    conn.close()

    # --decisions batch succeeds (both decided in one resume).
    decisions = json.dumps(
        [{"approval_id": "tu_1", "decision": "allow"}, {"approval_id": "tu_2", "decision": "allow"}]
    )
    rc = cli.main(["approve", "--outcome", run_id, "--decisions", decisions, "--store-dir", sd])
    out2 = capsys.readouterr().out
    assert rc == 0 and "status         : done" in out2
    conn = db.connect(f"{sd}/runs/{run_id}.db")
    tk = db.get_ticket(conn, "T-1042")
    assert tk["status"] == "pending_refund" and tk["queue"] == "billing"
    conn.close()


def test_cli_handle_example_file(monkeypatch, tmp_path, capsys) -> None:
    _install_stub(monkeypatch, [_step("done")])
    sd = str(tmp_path)
    example = tmp_path / "ticket.json"
    example.write_text(json.dumps({"ticket": "hello"}), encoding="utf-8")
    rc = cli.main(["handle", "--example", str(example), "--store-dir", sd])
    assert rc == 0
    assert "status         : done" in capsys.readouterr().out


def test_cli_seed_reports_counts(capsys) -> None:
    rc = cli.main(["seed", "--reset"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "8 customers" in out and "kb_chunks" in out


def test_cli_missing_key_surfaces_cleanly(monkeypatch, tmp_path, capsys) -> None:
    """A missing key surfaces as a clean message + nonzero exit, never a crash (§20)."""
    from relay.provider.base import MissingAPIKeyError

    def _boom(provider, model):
        raise MissingAPIKeyError("ANTHROPIC_API_KEY is not set.")

    monkeypatch.setattr("relay.agent.make_provider", _boom)
    rc = cli.main(["handle", "--ticket", "x", "--store-dir", str(tmp_path)])
    assert rc == 3
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_cli_json_output(monkeypatch, tmp_path, capsys) -> None:
    _install_stub(monkeypatch, [_step("done")])
    rc = cli.main(["handle", "--ticket", "x", "--json", "--store-dir", str(tmp_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "done" and payload["id"] == payload["run_id"]
