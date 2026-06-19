"""T5 — the leaderboard printer (Split 06 R3). Canned records → assert structure + labels.

Distributional numbers come from an LLM, so we assert the *shape* (deterministic line at 100%,
both providers side by side, mean ± spread, cost/latency, a separate frozen-slice line) — never
exact LLM values.
"""

from __future__ import annotations

from eval.metrics import PredictedAction, Prediction
from eval.run import MetricResults, RunRecord, aggregate, render_leaderboard


def _det(scenario_id: str) -> RunRecord:
    return RunRecord(
        scenario_id=scenario_id,
        split="must_gate",
        tier="deterministic",
        provider="stub",
        model="stub-model",
        prompt_version="relay-prompts-v1",
        policy="strict",
        repeat=1,
        run_id=f"det_{scenario_id}",
        prediction=Prediction(
            status="awaiting_approval",
            actions=[PredictedAction(tool="update_ticket", args={}, state="pending")],
        ),
        metrics=MetricResults(
            no_unapproved_writes=True,
            gate_policy_correct=True,
            schema_valid=True,
            paused_as_required=True,
        ),
    )


def _dist(
    scenario_id: str,
    provider: str,
    repeat: int,
    split: str,
    *,
    routing: bool,
    action: bool,
    faith: bool | None,
    cost: float,
    latency: float,
) -> RunRecord:
    return RunRecord(
        scenario_id=scenario_id,
        split=split,
        tier="distributional",
        provider=provider,
        model=f"{provider}-model",
        prompt_version="relay-prompts-v1",
        policy="default",
        repeat=repeat,
        run_id=f"{scenario_id}_{provider}_{repeat}",
        prediction=Prediction(
            status="done",
            intent="billing_dispute",
            priority="high",
            fields={"customer_email": "jane@acme.com"},
            cost_usd=cost,
            latency_s=latency,
        ),
        metrics=MetricResults(
            routing=routing,
            action=action,
            faithfulness=faith,
            fields={"customer_email": True},
            no_unapproved_writes=True,
            gate_policy_correct=True,
            schema_valid=True,
        ),
    )


def _canned() -> list[RunRecord]:
    records: list[RunRecord] = [_det(f"mg_{i}") for i in range(3)]
    for prov in ("anthropic", "openai"):
        for r in (1, 2, 3):
            records.append(
                _dist(
                    "t_billing_01",
                    prov,
                    r,
                    "tuning",
                    routing=True,
                    action=True,
                    faith=True,
                    cost=0.005,
                    latency=1.2,
                )
            )
            records.append(
                _dist(
                    "h_billing_01",
                    prov,
                    r,
                    "held_out",
                    routing=(r != 2),
                    action=True,
                    faith=None,
                    cost=0.03,
                    latency=3.4,
                )
            )
    return records


def test_leaderboard_has_both_registers_and_frozen_line() -> None:
    lb = aggregate(_canned())
    text = render_leaderboard(lb)

    # Deterministic register at 100%.
    assert "DETERMINISTIC SAFETY" in text
    assert "Never-acts-without-approval" in text and "100.0%" in text
    assert "Gate-policy correctness" in text
    assert "Schema validity" in text
    assert "must_gate frozen subset" in text

    # Distributional register, both providers, mean ± spread.
    assert "DISTRIBUTIONAL QUALITY" in text
    assert "anthropic" in text and "openai" in text
    assert "Routing acc" in text and "Action correct" in text and "Faithfulness" in text
    assert "+/-" in text
    assert "Extraction (per field):" in text and "customer_email" in text

    # Cost / latency per provider.
    assert "COST / LATENCY" in text and "$/ticket" in text and "p50" in text and "p95" in text

    # Frozen slice reported separately.
    assert "FROZEN HELD-OUT SLICE" in text


def test_deterministic_ratios_are_100pct() -> None:
    lb = aggregate(_canned())
    assert lb.never_acts.pct == 100.0
    assert lb.gate_policy.pct == 100.0
    assert lb.schema_validity.pct == 100.0
    assert lb.must_gate_paused.passed == lb.must_gate_paused.total == 3


def test_distributional_spread_reflects_repeat_variation() -> None:
    lb = aggregate(_canned())
    by_provider = {p.provider: p for p in lb.providers}
    # held-out routing missed on repeat 2 (of 3) for both providers → mean < 1, spread > 0.
    frozen = {p.provider: p for p in lb.frozen}
    for prov in ("anthropic", "openai"):
        assert by_provider[prov].routing is not None
        assert frozen[prov].routing is not None
        assert frozen[prov].routing.mean < 1.0
        assert frozen[prov].routing.spread > 0.0


def test_no_single_value_quality_claim() -> None:
    """E3 honesty: every distributional cell renders as mean ± spread, never a bare number."""
    lb = aggregate(_canned())
    text = render_leaderboard(lb)
    # Each metric row carries the +/- separator.
    for line in text.splitlines():
        if line.strip().startswith(("Routing acc", "Action correct", "Faithfulness")):
            assert "+/-" in line
