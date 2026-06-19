"""Split 04 faithfulness: the §10 scoring check (T1) and its wiring into the draft path (T2/T3).

All no-key (Tier-1): the LLM judge is a ``StubProvider`` playing canned per-claim labels, so
``all_grounded`` derivation and the loop wiring are exercised deterministically. The real judge
lives in ``test_faithfulness_api.py`` (Tier-2, ``@api``).
"""

from __future__ import annotations

import pytest

from relay import handle
from relay.agent import approve, assert_no_unapproved_writes
from relay.backend import db
from relay.cost import Usage, compute_cost
from relay.faithfulness import FaithfulnessResult, build_source, check
from relay.models import ClaimVerdict, Faithfulness, Triage
from relay.provider import StubProvider
from relay.provider.base import ModelStep, NormalizedToolCall

# --- builders ----------------------------------------------------------------


def _conn(store_dir: str, run_id: str):
    return db.connect(f"{store_dir}/runs/{run_id}.db")


def _triage() -> Triage:
    return Triage.model_validate(
        {
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
    )


def _verdict(*labels: str, all_grounded: bool = True) -> Faithfulness:
    """A canned faithfulness result. ``all_grounded`` is intentionally settable so we can prove
    ``check()`` recomputes it from the labels rather than trusting the model's flag."""
    return Faithfulness(
        all_grounded=all_grounded,
        claims=[ClaimVerdict(claim=f"claim {i}", label=lbl) for i, lbl in enumerate(labels)],
    )


def _call(call_id: str, name: str, **args: object) -> NormalizedToolCall:
    return NormalizedToolCall(id=call_id, name=name, args=dict(args))


def _step(text: str, *calls: NormalizedToolCall, usage: Usage | None = None) -> ModelStep:
    return ModelStep(
        text=text,
        tool_calls=list(calls),
        usage=usage or Usage(input_tokens=100, output_tokens=10),
        stop_reason="tool_use" if calls else "end_turn",
    )


@pytest.fixture()
def store(tmp_path) -> str:
    return str(tmp_path)


# --- T1: faithfulness scoring (canned) ---------------------------------------


def test_t1_all_supported_is_grounded() -> None:
    stub = StubProvider(structured_results=[_verdict("SUPPORTED", "SUPPORTED", all_grounded=False)])
    verdict, usage = check("body", [{"chunk_id": "kb-1", "text": "policy"}], stub)
    # Recomputed from labels: every claim SUPPORTED ⇒ grounded (the model's False flag is ignored).
    assert verdict.all_grounded is True
    assert [c.label for c in verdict.claims] == ["SUPPORTED", "SUPPORTED"]
    assert isinstance(usage, Usage)


def test_t1_one_contradicted_is_not_grounded() -> None:
    stub = StubProvider(
        structured_results=[_verdict("SUPPORTED", "CONTRADICTED", all_grounded=True)]
    )
    verdict, _ = check("body", [{"chunk_id": "kb-1", "text": "policy"}], stub)
    # The model lied (all_grounded=True); check() overrides to False from the labels.
    assert verdict.all_grounded is False


def test_t1_one_not_enough_info_is_not_grounded() -> None:
    stub = StubProvider(structured_results=[_verdict("SUPPORTED", "NOT_ENOUGH_INFO")])
    verdict, _ = check("body", [{"chunk_id": "kb-1", "text": "policy"}], stub)
    assert verdict.all_grounded is False


def test_t1_empty_claims_is_vacuously_grounded() -> None:
    stub = StubProvider(structured_results=[_verdict(all_grounded=False)])
    verdict, _ = check("a reply with no factual claims", [], stub)
    assert verdict.claims == []
    assert verdict.all_grounded is True  # vacuous truth: "every claim is SUPPORTED" over none


def test_faithfulness_result_is_the_models_faithfulness() -> None:
    # The spec's R1 `FaithfulnessResult` name resolves to the canonical models.Faithfulness.
    assert FaithfulnessResult is Faithfulness


# --- build_source formatting --------------------------------------------------


def test_build_source_lists_cited_chunks_then_reply() -> None:
    out = build_source(
        "We refund duplicate charges in 5-7 days.",
        [{"chunk_id": "kb-refund-001", "source": "refund-policy", "text": "Refunded in 5-7 days."}],
    )
    assert "SOURCE:" in out
    assert "[kb-refund-001] (refund-policy) Refunded in 5-7 days." in out
    assert out.rstrip().endswith("DRAFT REPLY:\nWe refund duplicate charges in 5-7 days.")


def test_build_source_handles_no_citations() -> None:
    out = build_source("unsupported claim", [])
    assert "(no sources were cited)" in out
    assert out.endswith("DRAFT REPLY:\nunsupported claim")


# --- T2: faithfulness wiring (populated, logged, costed, not a gate input) -----


def test_t2_faithfulness_wired_into_draft_and_costed(store: str) -> None:
    steps = [
        _step(
            "draft",
            _call("d", "draft_reply", body="We refund in 5-7 days.", citations=["kb-refund-001"]),
        ),
        _step("write", _call("w", "update_ticket", ticket_id="T-1042", status="pending_refund")),
        _step("done"),
    ]
    stub = StubProvider(
        triage_result=_triage(),
        structured_results=[_verdict("SUPPORTED")],  # the faithfulness call pops this
        steps=steps,
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    out = handle("charged twice", provider="stub", run_id="f2", store_dir=store, _provider=stub)

    # The verdict rode onto the Outcome's drafted reply.
    assert out.draft_reply is not None
    assert out.draft_reply.faithfulness is not None
    assert out.draft_reply.faithfulness.all_grounded is True
    assert [c.label for c in out.draft_reply.faithfulness.claims] == ["SUPPORTED"]
    # Faithfulness is NOT a gate input: the write still paused regardless of all_grounded.
    assert out.status == "awaiting_approval"
    assert [p.tool for p in out.actions_pending] == ["update_ticket"]

    conn = _conn(store, "f2")
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM llm_calls ORDER BY id").fetchall()]
    assert kinds == ["triage", "loop_step", "faithfulness", "loop_step"]
    db_sum = conn.execute("SELECT SUM(cost_usd) FROM llm_calls").fetchone()[0]
    faith_cost = conn.execute(
        "SELECT cost_usd FROM llm_calls WHERE kind='faithfulness'"
    ).fetchone()[0]
    assert faith_cost > 0.0  # the faithfulness inference is priced (anthropic model)
    assert out.cost_usd == pytest.approx(db_sum)  # $/ticket includes the faithfulness call (§13)
    expected_faith = compute_cost("anthropic", "claude-sonnet-4-6", stub._usage)
    assert faith_cost == pytest.approx(expected_faith)
    conn.close()


def test_t2_verdict_is_surfaced_back_to_the_model(store: str) -> None:
    """The faithfulness verdict is merged into the draft_reply tool_result fed back to the model
    on the next turn (§20: it *may* revise). We read it from the persisted transcript the model
    received — the bare ``tool_calls`` trace records the tool's own output (no verdict; the check
    runs in the orchestrator, not the tool)."""
    import json

    steps = [
        _step("draft", _call("d", "draft_reply", body="b", citations=["kb-refund-001"])),
        # A paused write persists the transcript (which already holds turn 1's draft tool_result).
        _step("write", _call("w", "update_ticket", ticket_id="T-1042", status="pending_refund")),
        _step("done"),
    ]
    stub = StubProvider(
        triage_result=_triage(),
        structured_results=[_verdict("CONTRADICTED", all_grounded=True)],
        steps=steps,
    )
    handle("x", provider="stub", run_id="f2b", store_dir=store, _provider=stub)

    conn = _conn(store, "f2b")
    state = json.loads(conn.execute("SELECT messages_json FROM runs WHERE id='f2b'").fetchone()[0])
    conn.close()
    # Find the tool_result block for the draft (id "d") in the transcript and parse its content.
    blocks = [
        b
        for msg in state["messages"]
        if isinstance(msg.get("content"), list)
        for b in msg["content"]
        if b.get("type") == "tool_result" and b.get("tool_use_id") == "d"
    ]
    assert blocks, "the draft tool_result must be in the transcript the model saw"
    payload = json.loads(blocks[0]["content"])
    assert (
        payload["faithfulness"]["all_grounded"] is False
    )  # recomputed from the CONTRADICTED label


# --- T3: all_grounded=false surfaces; loop may re-draft; gate unaffected (E4) --


def test_t3_ungrounded_reply_surfaces_without_blocking(store: str) -> None:
    steps = [
        _step(
            "draft",
            _call(
                "d",
                "draft_reply",
                body="invented a 30-day refund window",
                citations=["kb-refund-001"],
            ),
        ),
        _step("done — no further action"),
    ]
    stub = StubProvider(
        triage_result=_triage(),
        structured_results=[_verdict("NOT_ENOUGH_INFO", all_grounded=True)],
        steps=steps,
    )
    out = handle("x", provider="stub", run_id="f3", store_dir=store, _provider=stub)
    # Surfaced honestly: all_grounded false, per-claim list present; nothing crashed or blocked.
    assert out.status == "done"
    assert out.draft_reply is not None
    assert out.draft_reply.faithfulness.all_grounded is False
    assert out.draft_reply.faithfulness.claims[0].label == "NOT_ENOUGH_INFO"


def test_t3_loop_may_redraft_after_ungrounded(store: str) -> None:
    steps = [
        _step("draft 1", _call("d1", "draft_reply", body="bad", citations=[])),
        _step("draft 2", _call("d2", "draft_reply", body="good", citations=["kb-refund-001"])),
        _step("done"),
    ]
    stub = StubProvider(
        triage_result=_triage(),
        # First draft ungrounded; second grounded — the loop re-drafts via draft_reply.
        structured_results=[_verdict("CONTRADICTED"), _verdict("SUPPORTED")],
        steps=steps,
    )
    out = handle("x", provider="stub", run_id="f3b", store_dir=store, _provider=stub)
    assert out.status == "done"
    # The Outcome reflects the LAST (grounded) draft.
    assert out.draft_reply.body == "good"
    assert out.draft_reply.faithfulness.all_grounded is True

    conn = _conn(store, "f3b")
    n_faith = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE kind='faithfulness'").fetchone()[0]
    assert n_faith == 2  # one check per draft
    conn.close()


def test_e4_faithfulness_is_not_a_gate_input(store: str) -> None:
    """An ungrounded draft followed by a write: the write still pauses at the gate. Grounding and
    approval are orthogonal (§8 vs §14)."""
    steps = [
        _step("draft", _call("d", "draft_reply", body="ungrounded", citations=[])),
        _step("write", _call("w", "update_ticket", ticket_id="T-1042", status="pending_refund")),
        _step("done"),
    ]
    stub = StubProvider(
        triage_result=_triage(),
        structured_results=[_verdict("CONTRADICTED")],
        steps=steps,
    )
    out = handle("x", provider="stub", run_id="f4", store_dir=store, _provider=stub)
    assert out.draft_reply.faithfulness.all_grounded is False
    assert out.status == "awaiting_approval"  # the write paused despite the ungrounded reply
    conn = _conn(store, "f4")
    assert db.get_ticket(conn, "T-1042")["status"] == "open"  # nothing fired
    assert_no_unapproved_writes("f4", conn)
    conn.close()


# --- T7 regression: invariant still holds with faithfulness wired -------------


def test_t7_invariant_holds_with_faithfulness_wired(store: str) -> None:
    """A draft (read_class, faithfulness-checked) + a paused write: adding the check opened no
    un-approved write path; the write fires only on approve()."""
    steps = [
        _step("draft", _call("d", "draft_reply", body="b", citations=["kb-refund-001"])),
        _step("write", _call("w", "update_ticket", ticket_id="T-1042", status="pending_refund")),
        _step("done"),
    ]
    stub = StubProvider(
        triage_result=_triage(), structured_results=[_verdict("SUPPORTED")], steps=steps
    )
    out = handle("x", provider="stub", run_id="f7", store_dir=store, _provider=stub)
    conn = _conn(store, "f7")
    assert_no_unapproved_writes("f7", conn)  # holds in the suspended state
    assert (
        conn.execute("SELECT COUNT(*) FROM tool_calls WHERE tool='update_ticket'").fetchone()[0]
        == 0
    )
    conn.close()

    out2 = approve(
        "f7",
        [{"approval_id": out.actions_pending[0].id, "decision": "allow"}],
        store_dir=store,
        _provider=stub,
    )
    assert out2.status == "done"
    # The resumed Outcome still carries the faithfulness verdict from the earlier draft.
    assert out2.draft_reply is not None and out2.draft_reply.faithfulness.all_grounded is True
    conn = _conn(store, "f7")
    assert db.get_ticket(conn, "T-1042")["status"] == "pending_refund"
    assert_no_unapproved_writes("f7", conn)
    conn.close()
