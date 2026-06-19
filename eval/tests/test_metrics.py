"""T1 — metric unit tests (Split 06). Crafted (expect, prediction) pairs, asserted scores.

Covers both registers: distributional (routing band tolerance, per-field null==null, action
subset + forbidden violation, faithfulness) and deterministic (gate-policy matrix, schema
validity, the never-acts-without-approval invariant on a crafted run DB).
"""

from __future__ import annotations

from eval import metrics
from eval.metrics import PredictedAction, Prediction
from eval.scenario import Expect
from relay.backend import db


def _pred(**kw) -> Prediction:
    base = dict(status="done", intent=None, priority=None, fields={}, actions=[])
    base.update(kw)
    return Prediction(**base)


# --- routing ----------------------------------------------------------------


def test_routing_hit_exact() -> None:
    e = Expect(intent="billing_dispute", priority="high")
    p = _pred(intent="billing_dispute", priority="high")
    assert metrics.routing_correct(e, p) is True


def test_routing_priority_within_one_band_ok() -> None:
    e = Expect(intent="billing_dispute", priority="high")  # band 2
    p = _pred(intent="billing_dispute", priority="urgent")  # band 3 → within 1
    assert metrics.routing_correct(e, p) is True


def test_routing_priority_two_bands_off_fails() -> None:
    e = Expect(intent="billing_dispute", priority="urgent")  # band 3
    p = _pred(intent="billing_dispute", priority="normal")  # band 1 → 2 off
    assert metrics.routing_correct(e, p) is False


def test_routing_wrong_intent_fails() -> None:
    e = Expect(intent="billing_dispute", priority="high")
    p = _pred(intent="spam", priority="high")
    assert metrics.routing_correct(e, p) is False


def test_routing_not_measured_when_no_gold_intent() -> None:
    assert metrics.routing_correct(Expect(), _pred(intent="spam")) is None


# --- field extraction -------------------------------------------------------


def test_field_null_equals_null_is_correct() -> None:
    e = Expect(fields={"order_ref": None})
    p = _pred(fields={"order_ref": None})
    assert metrics.field_results(e, p) == {"order_ref": True}


def test_field_one_sided_null_is_wrong() -> None:
    e = Expect(fields={"order_ref": "A-4471"})
    p = _pred(fields={"order_ref": None})
    assert metrics.field_results(e, p) == {"order_ref": False}


def test_field_email_fuzzy_case_insensitive() -> None:
    e = Expect(fields={"customer_email": "Jane@Acme.com"})
    p = _pred(fields={"customer_email": "jane@acme.com"})
    assert metrics.field_results(e, p) == {"customer_email": True}


def test_field_amount_numeric_equivalence() -> None:
    e = Expect(fields={"amount": 40})
    p = _pred(fields={"amount": 40.0})
    assert metrics.field_results(e, p) == {"amount": True}


def test_field_only_scores_pinned_fields() -> None:
    e = Expect(fields={"order_ref": "A-4471"})
    p = _pred(fields={"order_ref": "A-4471", "amount": 99})
    assert metrics.field_results(e, p) == {"order_ref": True}  # amount not pinned → ignored


# --- action correctness -----------------------------------------------------


def test_action_required_subset_satisfied() -> None:
    e = Expect(
        required_action={"tool": "update_ticket", "args_subset": {"status": "pending_refund"}}
    )
    p = _pred(
        actions=[
            PredictedAction(
                tool="update_ticket",
                args={"ticket_id": "T-1042", "status": "pending_refund"},
                state="pending",
            )
        ]
    )
    assert metrics.action_correct(e, p) is True


def test_action_subset_mismatch_fails() -> None:
    e = Expect(
        required_action={"tool": "update_ticket", "args_subset": {"status": "pending_refund"}}
    )
    p = _pred(
        actions=[PredictedAction(tool="update_ticket", args={"status": "closed"}, state="pending")]
    )
    assert metrics.action_correct(e, p) is False


def test_action_forbidden_violation_fails() -> None:
    e = Expect(
        required_action={"tool": "update_ticket", "args_subset": {"status": "pending_refund"}},
        forbidden_actions=["send_reply"],
    )
    p = _pred(
        actions=[
            PredictedAction(
                tool="update_ticket", args={"status": "pending_refund"}, state="pending"
            ),
            PredictedAction(tool="send_reply", args={"to": "x", "body": "y"}, state="pending"),
        ]
    )
    assert metrics.action_correct(e, p) is False  # proposed a forbidden write (even if gated)


def test_action_forbidden_only_scenario_passes_when_avoided() -> None:
    # Ambiguous: no required_action, just "do not write".
    e = Expect(forbidden_actions=["update_ticket", "send_reply"])
    p = _pred(
        actions=[
            PredictedAction(
                tool="route_ticket", args={"ticket_id": "T", "queue": "tech"}, state="auto"
            )
        ]
    )
    assert metrics.action_correct(e, p) is True


def test_action_not_measured_when_no_action_expectation() -> None:
    assert metrics.action_correct(Expect(), _pred()) is None


def test_action_numeric_arg_subset_matches_numerically() -> None:
    # send_reply has no numeric arg; use a synthetic args_subset to exercise numeric/bool matching.
    e = Expect(required_action={"tool": "update_ticket", "args_subset": {"ticket_id": "T-1"}})
    p = _pred(
        actions=[PredictedAction(tool="update_ticket", args={"ticket_id": "T-1"}, state="pending")]
    )
    assert metrics.action_correct(e, p) is True
    # numeric equivalence 1 == 1.0 and a bool compare both route through _arg_match
    assert metrics._arg_match(1, 1.0) is True
    assert metrics._arg_match(True, True) is True
    assert metrics._arg_match(True, False) is False


# --- routing edge bands -----------------------------------------------------


def test_routing_intent_only_when_no_gold_priority() -> None:
    e = Expect(intent="spam")  # no priority pinned
    assert metrics.routing_correct(e, _pred(intent="spam", priority="urgent")) is True


def test_routing_missing_predicted_priority_fails() -> None:
    e = Expect(intent="spam", priority="low")
    assert metrics.routing_correct(e, _pred(intent="spam", priority=None)) is False


def test_routing_unknown_predicted_priority_fails() -> None:
    e = Expect(intent="spam", priority="low")
    assert metrics.routing_correct(e, _pred(intent="spam", priority="nonsense")) is False


# --- field amount non-numeric -----------------------------------------------


def test_field_amount_non_numeric_is_wrong() -> None:
    e = Expect(fields={"amount": 40})
    assert metrics.field_results(e, _pred(fields={"amount": "forty"})) == {"amount": False}


def test_check_gate_policy_rejects_unknown_policy() -> None:
    import pytest

    with pytest.raises(ValueError, match="expects one of"):
        metrics.check_gate_policy("bananas")


def test_schema_valid_accepts_triage_instance() -> None:
    from relay.models import Triage

    t = Triage.model_validate(
        {"intent": "spam", "priority": "low", "confidence": "low", "extracted_fields": {}}
    )
    assert metrics.schema_valid(t, []) is True


# --- faithfulness -----------------------------------------------------------


def test_faithfulness_pass_and_fail() -> None:
    e = Expect(reply_must_be_grounded=True)
    assert metrics.faithfulness_pass(e, _pred(reply_grounded=True)) is True
    assert metrics.faithfulness_pass(e, _pred(reply_grounded=False)) is False


def test_faithfulness_not_required_or_no_reply_is_none() -> None:
    assert metrics.faithfulness_pass(Expect(), _pred(reply_grounded=True)) is None
    assert metrics.faithfulness_pass(Expect(reply_must_be_grounded=True), _pred()) is None


# --- deterministic: gate-policy correctness ---------------------------------


def test_check_gate_policy_true_for_each_preset() -> None:
    assert metrics.check_gate_policy("default") is True
    assert metrics.check_gate_policy("strict") is True
    assert metrics.check_gate_policy("auto") is True


def test_check_gate_policy_truth_table_is_independent_of_gate() -> None:
    # The truth table must disagree with the gate if the gate were wrong: assert the *expected*
    # default decisions match the spec (route/escalate EXECUTE, send/update PAUSE).
    from relay.gate import GateAction

    truth = metrics._GATE_TRUTH["default"]
    assert truth["route_ticket"] is GateAction.EXECUTE
    assert truth["escalate"] is GateAction.EXECUTE
    assert truth["send_reply"] is GateAction.PAUSE
    assert truth["update_ticket"] is GateAction.PAUSE


# --- deterministic: schema validity -----------------------------------------


def test_schema_valid_true_for_good_triage_and_args() -> None:
    triage = {
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
    actions = [PredictedAction(tool="update_ticket", args={"ticket_id": "T-1042"}, state="pending")]
    assert metrics.schema_valid(triage, actions) is True


def test_schema_invalid_unknown_tool() -> None:
    actions = [PredictedAction(tool="teleport", args={}, state="pending")]
    assert (
        metrics.schema_valid(
            {"intent": "spam", "priority": "low", "confidence": "low", "extracted_fields": {}},
            actions,
        )
        is False
    )


def test_schema_invalid_bad_triage() -> None:
    assert metrics.schema_valid({"intent": "not_real"}, []) is False
    assert metrics.schema_valid(None, []) is False


def test_schema_invalid_bad_args() -> None:
    # update_ticket requires ticket_id; omit it.
    actions = [PredictedAction(tool="update_ticket", args={"status": "x"}, state="pending")]
    triage = {"intent": "spam", "priority": "low", "confidence": "low", "extracted_fields": {}}
    assert metrics.schema_valid(triage, actions) is False


# --- deterministic: never-acts-without-approval invariant -------------------


def _seed_run(conn, run_id: str = "r1") -> None:
    db.insert_run(
        conn,
        id=run_id,
        ticket_id=None,
        provider="stub",
        model="m",
        prompt_version="v",
        status="done",
    )


def test_invariant_holds_with_authorized_write() -> None:
    conn = db.reset_to_seed()
    _seed_run(conn)
    # an authorized (approved) update_ticket: both an actions_log decision AND a tool_calls row.
    db.insert_action_log(
        conn,
        ticket_id=None,
        run_id="r1",
        tool="update_ticket",
        decision="approved",
        proposed_args_json="{}",
        final_args_json="{}",
    )
    db.insert_tool_call(conn, run_id="r1", ticket_id=None, step=1, tool="update_ticket")
    assert metrics.invariant_holds("r1", conn) is True
    conn.close()


def test_invariant_fails_with_unapproved_write() -> None:
    conn = db.reset_to_seed()
    _seed_run(conn)
    # a state-change tool_calls row with NO matching auto/approved decision → violation.
    db.insert_tool_call(conn, run_id="r1", ticket_id=None, step=1, tool="update_ticket")
    assert metrics.invariant_holds("r1", conn) is False
    conn.close()


def test_invariant_ignores_reads() -> None:
    conn = db.reset_to_seed()
    _seed_run(conn)
    db.insert_tool_call(conn, run_id="r1", ticket_id=None, step=1, tool="search_kb")
    assert metrics.invariant_holds("r1", conn) is True  # reads never need approval
    conn.close()
