"""Relay CLI (spec §16) — the feature-complete command surface (Split 04).

``handle`` runs the gated loop on a ticket and prints triage, the trace, the drafted reply with
its **faithfulness line** (``✓ Grounded (n/n)``), any paused ``ask`` action (args + rationale +
run id), and the ``$/ticket``/latency caption — or the whole ``Outcome`` as JSON with ``--json``.
``approve`` (a *second* process invocation) reopens that run by id and fires/rejects the
decision(s) (single ``--approval`` shorthand or a ``--decisions`` batch). ``seed --reset``
rebuilds a seed DB for inspection. ``eval`` is a stub until Split 06.

The whole Anthropic engine is now operable from the terminal with no Python REPL. Errors are
surfaced honestly (missing key → exit 3; usage/backend/refusal/bad-JSON → exit 2), never a raw
traceback (§20).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .agent import approve, handle
from .backend import db
from .models import Faithfulness, Outcome
from .provider.base import MissingAPIKeyError, ProviderError


def _faithfulness_line(verdict: Faithfulness | None) -> str:
    """One-line grounding caption for the drafted reply (E1: ``✓ Grounded (n/n)``)."""
    if verdict is None:
        return "(not checked)"
    total = len(verdict.claims)
    supported = sum(1 for c in verdict.claims if c.label == "SUPPORTED")
    mark = "✓ Grounded" if verdict.all_grounded else "⚠ NOT grounded"
    line = f"{mark} ({supported}/{total})"
    if not verdict.all_grounded:
        flagged = [f"{c.label}: {c.claim!r}" for c in verdict.claims if c.label != "SUPPORTED"]
        if flagged:
            line += "  — " + "; ".join(flagged)
    return line


def _read_ticket(args: argparse.Namespace) -> str:
    if args.ticket is not None:
        return args.ticket
    data = json.loads(Path(args.example).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "ticket" in data:
        return str(data["ticket"])
    raise ValueError(f"example file {args.example!r} must contain a top-level 'ticket' field")


def _format_outcome(outcome: Outcome) -> str:
    t = outcome.triage
    lines = [
        f"run/outcome id : {outcome.id}",
        f"status         : {outcome.status}",
        f"provider/model : {outcome.provider} / {outcome.model}  (prompt {outcome.prompt_version})",
        f"triage         : intent={t.intent.value} priority={t.priority.value} "
        f"confidence={t.confidence.value}",
        f"cost_usd       : ${outcome.cost_usd:.6f}    latency_s: {outcome.latency_s}",
    ]
    if outcome.draft_reply is not None:
        cites = ", ".join(c.chunk_id for c in outcome.draft_reply.citations) or "(none)"
        lines.append(f"draft_reply    : {outcome.draft_reply.body!r} [cites: {cites}]")
        lines.append(f"faithfulness   : {_faithfulness_line(outcome.draft_reply.faithfulness)}")
    if outcome.actions_taken:
        lines.append("actions taken  :")
        for a in outcome.actions_taken:
            lines.append(f"  - {a.tool} ({a.decision.value}) args={json.dumps(a.args)}")
    if outcome.actions_pending:
        lines.append("PENDING APPROVAL (nothing fired — waiting on you):")
        for p in outcome.actions_pending:
            lines.append(f"  - approval_id={p.id}  tool={p.tool}  args={json.dumps(p.args)}")
            if p.rationale:
                lines.append(f"      rationale: {p.rationale}")
        ids = " ".join(p.id for p in outcome.actions_pending)
        if len(outcome.actions_pending) == 1:
            only = outcome.actions_pending[0].id
            lines.append(
                f"  approve with: relay approve --outcome {outcome.id} "
                f"--approval {only} --decision allow"
            )
        else:
            decisions = [
                {"approval_id": p.id, "decision": "allow"} for p in outcome.actions_pending
            ]
            lines.append(
                f"  multiple pending ({ids}) — approve all with: "
                f"relay approve --outcome {outcome.id} --decisions '{json.dumps(decisions)}'"
            )
    return "\n".join(lines)


def _cmd_handle(args: argparse.Namespace) -> int:
    ticket = _read_ticket(args)
    outcome = handle(
        ticket,
        provider=args.provider,
        model=args.model,
        policy=args.policy,
        approve_all=args.approve_all,
        store_dir=args.store_dir,
    )
    if args.json:
        print(outcome.model_dump_json(indent=2))
    else:
        print(_format_outcome(outcome))
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    if args.decisions is not None:
        decisions = json.loads(args.decisions)
        if not isinstance(decisions, list):
            raise ValueError(
                "--decisions must be a JSON array of {approval_id, decision[, edited_args]}"
            )
    else:
        if args.approval is None or args.decision is None:
            raise ValueError("provide --approval ID --decision allow|reject, or --decisions JSON")
        decision: dict[str, Any] = {"approval_id": args.approval, "decision": args.decision}
        if args.edit_args is not None:
            decision["edited_args"] = json.loads(args.edit_args)
        decisions = [decision]

    # The single-pending --approval form errors if the suspended turn has >1 pending (§16).
    if args.decisions is None:
        run = _peek_pending(args.outcome, args.store_dir)
        if run is not None and len(run) > 1:
            print(
                f"error: run {args.outcome} has {len(run)} pending actions; "
                f"use --decisions for the turn-granular batch (approval ids: {' '.join(run)})",
                file=sys.stderr,
            )
            return 2

    outcome = approve(args.outcome, decisions, store_dir=args.store_dir)
    if args.json:
        print(outcome.model_dump_json(indent=2))
    else:
        print(_format_outcome(outcome))
    return 0


def _peek_pending(outcome_id: str, store_dir: str | None) -> list[str] | None:
    """Return the pending approval ids of a suspended run (for the single-pending guard)."""
    from .agent import _run_db_path  # local import: same module owns the path scheme

    path = _run_db_path(outcome_id, store_dir)
    if not Path(path).exists():
        return None
    conn = db.connect(path)
    try:
        row = conn.execute("SELECT messages_json FROM runs WHERE id = ?", (outcome_id,)).fetchone()
        if row is None or row["messages_json"] is None:
            return None
        state = json.loads(row["messages_json"])
        return [p["id"] for p in state.get("pending", [])]
    finally:
        conn.close()


def _cmd_eval(args: argparse.Namespace) -> int:
    """Pointer, not an import: the eval harness lives in the top-level ``eval/`` layer.

    ``core`` depends on nothing (``00-conventions.md`` §1), so it must not import ``eval/``.
    The real entrypoint is ``python -m eval.run`` — print it (a plain string) and exit non-zero
    so scripts that expected a result here fail loudly rather than silently no-op.
    """
    print(
        "Eval lives in the eval/ layer: run `python -m eval.run` "
        "(--tier1 for the no-key safety gate, --quick for a fast smoke).",
        file=sys.stderr,
    )
    return 2


def _cmd_seed(args: argparse.Namespace) -> int:
    conn = db.reset_to_seed()
    try:
        customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        tickets = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]
        print(f"seeded a fresh DB: {customers} customers, {tickets} tickets, {chunks} kb_chunks")
    finally:
        conn.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="relay", description="Relay — gated ops-automation agent."
    )
    parser.add_argument("--version", action="version", version=f"relay {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    h = sub.add_parser("handle", help="triage a ticket and run the gated agent loop")
    src = h.add_mutually_exclusive_group(required=True)
    src.add_argument("--example", help="path to a JSON example with a top-level 'ticket' field")
    src.add_argument("--ticket", help="ticket text")
    h.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    h.add_argument("--model", default=None)
    h.add_argument("--policy", default="default", choices=["auto", "default", "strict"])
    h.add_argument("--approve-all", action="store_true", help="auto-execute all state-changes")
    h.add_argument("--store-dir", default=None, help="run-store directory (default: temp/env)")
    h.add_argument("--json", action="store_true", help="print the Outcome as JSON")
    h.set_defaults(func=_cmd_handle)

    a = sub.add_parser("approve", help="decide a suspended run's pending action(s) and resume")
    a.add_argument("--outcome", required=True, help="the run/outcome id to resume")
    a.add_argument("--approval", default=None, help="single-pending approval id (sugar)")
    a.add_argument("--decision", default=None, choices=["allow", "reject"])
    a.add_argument("--edit-args", default=None, help="JSON object to override the action args")
    a.add_argument("--decisions", default=None, help="JSON batch [{approval_id, decision, ...}]")
    a.add_argument("--store-dir", default=None, help="run-store directory (default: temp/env)")
    a.add_argument("--json", action="store_true", help="print the Outcome as JSON")
    a.set_defaults(func=_cmd_approve)

    s = sub.add_parser("seed", help="rebuild a seed DB (for inspection)")
    s.add_argument("--reset", action="store_true", help="(no-op flag; seeding always resets)")
    s.set_defaults(func=_cmd_seed)

    e = sub.add_parser("eval", help="pointer to the eval/ layer (`python -m eval.run`)")
    e.add_argument("--quick", action="store_true", help="(use `python -m eval.run --quick`)")
    e.set_defaults(func=_cmd_eval)
    return parser


def _make_output_safe() -> None:
    """Never crash on a console codepage that can't encode ``✓``/``⚠`` (§20).

    Reconfigure stdout/stderr to replace un-encodable glyphs instead of raising
    ``UnicodeEncodeError`` (e.g. a Windows cp1252 console). Guarded: capture streams in tests
    and already-UTF-8 streams are left untouched.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - defensive
                pass


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _make_output_safe()
    try:
        return int(args.func(args))
    except MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (ProviderError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - thin entrypoint
    raise SystemExit(main())
