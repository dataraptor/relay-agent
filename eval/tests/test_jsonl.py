"""T6 — jsonl persistence round-trip (Split 06 R3).

A run writes ``runs/*.jsonl``; reloading it yields records that still carry
``provider``/``model``/``prompt_version``/scenario-id/prediction/metrics.
"""

from __future__ import annotations

from pathlib import Path

from eval.run import read_jsonl, run_suite, write_jsonl
from eval.scenario import load_scenarios

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def test_jsonl_round_trip_carries_every_field(tmp_path: Path) -> None:
    scenarios = [s for s in load_scenarios(SCENARIOS_DIR) if s.split == "must_gate"][:4]
    records = run_suite(
        scenarios, distributional_providers=[], repeats=1, store_dir=str(tmp_path / "store")
    )
    out = tmp_path / "runs" / "test.jsonl"
    written = write_jsonl(records, out)
    assert written.exists()

    reloaded = read_jsonl(out)
    assert len(reloaded) == len(records)
    for r in reloaded:
        assert r.provider == "stub"
        assert r.model
        assert r.prompt_version == "relay-prompts-v1"
        assert r.scenario_id
        assert r.prediction is not None and r.prediction.status
        assert r.metrics is not None
        assert r.metrics.no_unapproved_writes is True
        assert r.run_id


def test_jsonl_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "a" / "b" / "c" / "x.jsonl"
    write_jsonl([], out)
    assert out.exists()
    assert read_jsonl(out) == []
