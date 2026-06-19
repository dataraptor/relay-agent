"""T2 — scenario schema + gold-set integrity (Split 06).

Every shipped ``*.yaml`` loads into a valid :class:`Scenario`, the frozen ``must_gate`` subset is
identifiable / non-empty / well-formed, the total count is in the ~30–40 range, and the loader
rejects the mistakes that would silently corrupt the numbers (bad labels, duplicate ids).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.scenario import FIELD_KEYS, Scenario, load_scenarios

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def _all() -> list[Scenario]:
    return load_scenarios(SCENARIOS_DIR)


def test_all_scenarios_load_and_count_in_range() -> None:
    scenarios = _all()
    assert 30 <= len(scenarios) <= 40, f"gold set should be ~30-40, got {len(scenarios)}"
    # ids are unique (the loader already enforces this; assert it held)
    ids = [s.id for s in scenarios]
    assert len(ids) == len(set(ids))


def test_splits_present_and_frozen_subset_nonempty() -> None:
    scenarios = _all()
    by_split: dict[str, list[Scenario]] = {}
    for s in scenarios:
        by_split.setdefault(s.split, []).append(s)
    assert set(by_split) == {"must_gate", "tuning", "held_out"}
    assert by_split["must_gate"], "must_gate (the safety contract) must be non-empty"
    # held-out is the ~20% frozen distributional slice.
    held_frac = len(by_split["held_out"]) / len(scenarios)
    assert 0.1 <= held_frac <= 0.35, f"held_out should be ~20%, got {held_frac:.0%}"


def test_must_gate_each_names_a_state_change_and_covers_all_four_tools() -> None:
    scenarios = [s for s in _all() if s.split == "must_gate"]
    tools = set()
    for s in scenarios:
        assert s.expect.required_action is not None, f"{s.id} must pin the write that pauses"
        tools.add(s.expect.required_action.tool)
    # The contract proves *every* state-change tool pauses under strict.
    assert tools == {"send_reply", "update_ticket", "route_ticket", "escalate"}


def test_expect_fields_use_only_known_keys() -> None:
    for s in _all():
        assert set(s.expect.fields) <= set(FIELD_KEYS)


def test_required_keys_present() -> None:
    for s in _all():
        assert s.id and s.ticket and s.expect is not None
        assert s.split in ("must_gate", "tuning", "held_out")


def test_loader_rejects_unknown_intent(tmp_path: Path) -> None:
    (tmp_path / "tuning").mkdir()
    (tmp_path / "tuning" / "bad.yaml").write_text(
        "id: x\nticket: hi\nexpect:\n  intent: not_an_intent\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_scenarios(tmp_path)


def test_loader_rejects_unknown_tool(tmp_path: Path) -> None:
    (tmp_path / "tuning").mkdir()
    (tmp_path / "tuning" / "bad.yaml").write_text(
        "id: x\nticket: hi\nexpect:\n  required_action:\n    tool: teleport\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_scenarios(tmp_path)


def test_loader_rejects_must_gate_without_required_action(tmp_path: Path) -> None:
    (tmp_path / "must_gate").mkdir()
    (tmp_path / "must_gate" / "bad.yaml").write_text(
        "id: x\nticket: hi\nexpect:\n  intent: billing_dispute\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="required_action"):
        load_scenarios(tmp_path)


def test_loader_rejects_duplicate_id(tmp_path: Path) -> None:
    (tmp_path / "tuning").mkdir()
    (tmp_path / "tuning" / "a.yaml").write_text(
        "id: dup\nticket: a\nexpect: {}\n", encoding="utf-8"
    )
    (tmp_path / "tuning" / "b.yaml").write_text(
        "id: dup\nticket: b\nexpect: {}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_scenarios(tmp_path)


def test_loader_supports_multi_scenario_file_and_folder_split(tmp_path: Path) -> None:
    (tmp_path / "held_out").mkdir()
    (tmp_path / "held_out" / "many.yaml").write_text(
        "- id: a\n  ticket: t\n  expect: {}\n- id: b\n  ticket: t\n  expect: {}\n", encoding="utf-8"
    )
    scenarios = load_scenarios(tmp_path)
    assert {s.id for s in scenarios} == {"a", "b"}
    assert all(s.split == "held_out" for s in scenarios)  # folder set the split
