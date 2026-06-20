"""Unit coverage for ``meta.py`` edge cases (examples override, malformed/missing files)."""

from __future__ import annotations

import json

from relay_api.meta import env_var_for, load_examples, provider_available


def test_examples_override_dir(monkeypatch, tmp_path) -> None:
    """``RELAY_EXAMPLES_DIR`` overrides the package-relative path; only valid files load."""
    monkeypatch.setenv("RELAY_EXAMPLES_DIR", str(tmp_path))
    (tmp_path / "billing_dispute.json").write_text(
        json.dumps({"ticket": "custom ticket text"}), encoding="utf-8"
    )
    (tmp_path / "injection.json").write_text("{ not valid json", encoding="utf-8")  # skipped
    (tmp_path / "tech_issue.json").write_text(
        json.dumps({"no_ticket": 1}), encoding="utf-8"
    )  # skip
    # ambiguous.json absent → skipped
    examples = load_examples()
    ids = {e["id"]: e for e in (x.model_dump() for x in examples)}
    assert set(ids) == {"billing_dispute"}
    assert ids["billing_dispute"]["ticket"] == "custom ticket text"


def test_examples_empty_when_dir_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RELAY_EXAMPLES_DIR", str(tmp_path / "nope"))
    assert load_examples() == []


def test_env_var_for_unknown_provider() -> None:
    assert env_var_for("anthropic") == "ANTHROPIC_API_KEY"
    assert env_var_for("openai") == "OPENAI_API_KEY"
    assert env_var_for("mystery") == ""


def test_provider_available_unknown_is_false() -> None:
    assert provider_available("mystery") is False
