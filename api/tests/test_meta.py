"""T5 — ``/examples`` + ``/config`` + ``/health`` (Tier-1, no key).

Provider facts (model IDs) are sourced from ``core`` — these tests pin them against Split 05's
exact strings so any drift between API and engine fails here (E5).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from relay.provider.anthropic import ANTHROPIC_MODELS
from relay.provider.openai import OPENAI_MODELS


def test_t5_examples_returns_core_fixtures(client: TestClient) -> None:
    examples = client.get("/examples").json()
    ids = {e["id"] for e in examples}
    assert ids == {"billing_dispute", "tech_issue", "ambiguous", "injection"}
    # Injection is the only locked example (UI §5.2).
    locks = {e["id"]: e["lock"] for e in examples}
    assert locks["injection"] is True
    assert locks["billing_dispute"] is False
    # Text comes from the real fixtures, not a hardcoded copy.
    billing = next(e for e in examples if e["id"] == "billing_dispute")
    assert "charged twice" in billing["ticket"].lower()


def test_t5_config_model_ids_match_core(client: TestClient) -> None:
    cfg = client.get("/config").json()
    assert cfg["providers"] == ["anthropic", "openai"]
    assert cfg["policies"] == ["auto", "default", "strict"]
    # Model IDs trace back to core (Split 05's pinned strings) — no drift.
    assert set(cfg["models_by_provider"]["anthropic"]) == set(ANTHROPIC_MODELS)
    assert set(cfg["models_by_provider"]["openai"]) == set(OPENAI_MODELS)
    assert cfg["default_provider"] == "anthropic"
    assert cfg["default_model_by_provider"]["anthropic"] == "claude-sonnet-4-6"
    assert cfg["default_model_by_provider"]["openai"] in OPENAI_MODELS


def test_t5_health_reflects_key_presence(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["providers_available"] == {"anthropic": False, "openai": False}

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    health2 = client.get("/health").json()
    assert health2["providers_available"] == {"anthropic": True, "openai": True}


def test_t5_health_counts_azure_for_openai(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azkey")
    health = client.get("/health").json()
    assert health["providers_available"]["openai"] is True
