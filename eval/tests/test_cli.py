"""No-key CLI tests for `python -m eval.run` (Split 06 R5).

Exercises the entrypoint's no-key-reachable paths: the deterministic ``--tier1`` gate (exit 0,
leaderboard printed, jsonl written), graceful handling when no provider key is available
(falls back to the deterministic tier with a printed note rather than crashing), and a clean
error on a missing scenarios directory.
"""

from __future__ import annotations

from pathlib import Path

from eval import run as run_mod


def test_tier1_cli_exits_zero_and_prints_leaderboard(tmp_path: Path, capsys) -> None:
    out = tmp_path / "runs" / "t1.jsonl"
    rc = run_mod.main(["--tier1", "--out", str(out)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "DETERMINISTIC SAFETY" in captured.out
    assert "100.0%" in captured.out
    assert out.exists()
    assert run_mod.read_jsonl(out)  # records persisted


def test_no_key_falls_back_to_deterministic(tmp_path: Path, capsys, monkeypatch) -> None:
    # Force both providers "unavailable" → a distributional run with no key must NOT crash; it
    # prints a note and runs the deterministic tier only (§20 "missing key → clear message").
    monkeypatch.setattr(run_mod, "provider_available", lambda name: False)
    out = tmp_path / "runs" / "nokey.jsonl"
    rc = run_mod.main(["--quick", "--provider", "both", "--out", str(out)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "no provider key available" in captured.err
    assert "DETERMINISTIC SAFETY" in captured.out
    assert "skipped" in captured.out  # distributional block skipped


def test_bad_scenarios_dir_exits_two(tmp_path: Path, capsys) -> None:
    rc = run_mod.main(["--tier1", "--scenarios", str(tmp_path / "does_not_exist")])
    assert rc == 2
    assert "error:" in capsys.readouterr().err


def test_quick_subset_is_tier_complete_and_small() -> None:
    from eval.scenario import load_scenarios

    scenarios = load_scenarios(run_mod.SCENARIOS_DIR)
    subset = run_mod.quick_subset(scenarios, per_split=2)
    splits = {s.split for s in subset}
    assert splits == {"must_gate", "tuning", "held_out"}  # exercises both tiers
    assert len(subset) <= 6
