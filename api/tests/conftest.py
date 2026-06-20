"""Shared test setup for the API layer.

Two jobs: (1) load a local ``.env`` so the Tier-2 (``@api``) tests can find a real provider key
— Tier-1 (no-key) tests never read it; (2) provide fixtures that wire a fresh, isolated app +
``TestClient`` around an injected ``StubProvider`` (the FastAPI dependency override), plus builders
for the worked billing scenario. Keys are never printed or committed (``.env`` is gitignored).
Mirrors ``core/tests/conftest.py``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from relay import ModelStep, StubProvider
from relay.cost import Usage
from relay.models import ExtractedFields, Triage
from relay.provider.base import NormalizedToolCall
from relay_api.app import create_app, provider_dependency
from relay_api.runs import RunStore


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_here = Path(__file__).resolve()
for candidate in (_here.parents[1] / ".env", _here.parents[2] / ".env"):
    _load_env_file(candidate)


# ---------------------------------------------------------------------------
# Builders for the worked billing scenario (no network)
# ---------------------------------------------------------------------------


def billing_triage() -> Triage:
    return Triage(
        intent="billing_dispute",
        priority="high",
        extracted_fields=ExtractedFields(
            customer_email="jane@acme.com", order_ref="A-4471", amount=None, product="Pro"
        ),
        confidence="medium",
    )


def call(tool: str, **args: object) -> NormalizedToolCall:
    return NormalizedToolCall(id=f"tc_{tool}", name=tool, args=dict(args))


def step(text: str, *calls: NormalizedToolCall, tokens: tuple[int, int] = (100, 20)) -> ModelStep:
    return ModelStep(
        text=text,
        tool_calls=list(calls),
        usage=Usage(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


def billing_steps() -> list[ModelStep]:
    """Read -> read -> draft -> propose update_ticket (the canonical money demo)."""
    return [
        step("Looking up the customer.", call("lookup_customer", email="jane@acme.com")),
        step("Checking refund policy.", call("search_kb", query="duplicate charge refund policy")),
        step(
            "Drafting a cited reply.",
            call(
                "draft_reply",
                body="We've confirmed the duplicate charge and will refund it in 5-7 days.",
                citations=["kb-refund-001"],
            ),
        ),
        step(
            "Proposing a status update for your approval.",
            call("update_ticket", ticket_id="T-1042", status="pending_refund", note="dup verified"),
        ),
    ]


def make_stub(
    steps: list[ModelStep] | None = None, *, triage: Triage | None = None
) -> StubProvider:
    """A priced stub (anthropic/sonnet) so the run's cost ledger accumulates a non-zero $/ticket."""
    return StubProvider(
        triage_result=triage or billing_triage(),
        steps=steps if steps is not None else billing_steps(),
        provider="anthropic",
        model="claude-sonnet-4-6",
        usage=Usage(input_tokens=80, output_tokens=16),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(base_dir=str(tmp_path / "runs-base"))


ClientFactory = Callable[[StubProvider], TestClient]


@pytest.fixture
def make_client(store: RunStore) -> ClientFactory:
    """Return a factory building a ``TestClient`` whose provider dependency yields the given stub,
    over a single shared (isolated) run store — so ``/handle`` and ``/approve`` reuse one store."""

    def _factory(stub: StubProvider) -> TestClient:
        app = create_app(store=store)
        app.dependency_overrides[provider_dependency] = lambda: stub
        return TestClient(app)

    return _factory


@pytest.fixture
def client(make_client: ClientFactory) -> TestClient:
    """A client wired to the canonical billing stub (the common case)."""
    return make_client(make_stub())
