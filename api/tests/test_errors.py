"""T6 — the structured error envelope (Tier-1, no key). Every §20 path is an envelope, not a 500."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from relay.provider.base import ProviderError
from relay_api.app import create_app
from relay_api.runs import RunStore


class _RaisingProvider:
    """A provider whose first inference raises — to exercise the error handlers (E4)."""

    provider = "anthropic"
    model = "claude-sonnet-4-6"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def structured_output(self, system: str, user: str, schema_model: Any) -> Any:
        raise self._exc

    def triage(self, ticket: str) -> Any:  # pragma: no cover - unused by the loop
        raise self._exc

    def step(self, messages: Any, tools: Any) -> Any:  # pragma: no cover - never reached
        raise self._exc


def test_t6_missing_key_envelope(store: RunStore, monkeypatch) -> None:
    """A real provider with no key → ``missing_key`` envelope naming the provider + env var, no
    traceback. Built WITHOUT a provider override so relay constructs the real (keyless) provider."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app(store=store)  # no dependency override → real provider construction
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/handle", json={"ticket": "charged twice", "provider": "anthropic"})
    assert resp.status_code == 424
    err = resp.json()["error"]
    assert err["type"] == "missing_key"
    assert err["provider"] == "anthropic"
    assert err["env_var"] == "ANTHROPIC_API_KEY"
    assert err["retriable"] is False


def test_t6_bad_provider_is_bad_request(client: TestClient) -> None:
    resp = client.post("/handle", json={"ticket": "x", "provider": "nope"})
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "bad_request"


def test_t6_bad_policy_is_bad_request(client: TestClient) -> None:
    resp = client.post("/handle", json={"ticket": "x", "policy": "loose"})
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "bad_request"


def test_t6_empty_ticket_is_bad_request(client: TestClient) -> None:
    resp = client.post("/handle", json={"ticket": ""})
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "bad_request"


def test_t6_malformed_decisions_is_bad_request(client: TestClient) -> None:
    """A ``/approve`` body missing required decision fields → ``400 bad_request``."""
    handle = client.post("/handle", json={"ticket": "charged twice", "policy": "strict"}).json()
    resp = client.post(
        "/approve", json={"run_id": handle["run_id"], "decisions": [{"approval_id": "x"}]}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "bad_request"


def test_t6_unknown_model_is_bad_request(store: RunStore, monkeypatch) -> None:
    """An unknown model id (real provider path) → ``bad_request`` (engine ValueError), not a 500."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")  # get past the key check
    app = create_app(store=store)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/handle", json={"ticket": "x", "provider": "anthropic", "model": "claude-nope"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "bad_request"


def test_t6_no_traceback_leaks(client: TestClient) -> None:
    """Error envelopes never carry a Python traceback / 'detail' field."""
    resp = client.post("/handle", json={"ticket": "x", "provider": "nope"})
    text = resp.text.lower()
    assert "traceback" not in text
    assert "detail" not in resp.json()  # our envelope shape, not FastAPI's default


def test_t6_provider_error_is_502(make_client) -> None:
    """A provider-layer failure (parse/refusal) → ``502 provider_error`` (retriable), not a 500."""
    client = make_client(_RaisingProvider(ProviderError("upstream parse failure")))
    resp = client.post("/handle", json={"ticket": "x"})
    assert resp.status_code == 502
    err = resp.json()["error"]
    assert err["type"] == "provider_error" and err["retriable"] is True


def test_t6_unexpected_error_is_neutral_500(store: RunStore) -> None:
    """An unexpected exception → a neutral ``internal_error`` envelope, never a leaked trace."""
    from relay_api.app import provider_dependency

    app = create_app(store=store)
    app.dependency_overrides[provider_dependency] = lambda: _RaisingProvider(RuntimeError("kaboom"))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/handle", json={"ticket": "x"})
    assert resp.status_code == 500
    err = resp.json()["error"]
    assert err["type"] == "internal_error"
    assert "kaboom" not in resp.text  # internal detail is NOT leaked
