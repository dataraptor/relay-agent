"""T8 — real end-to-end over HTTP (Tier-2, ``@api``; needs a key, auto-skipped otherwise).

Drives ``/handle`` → ``/approve`` against a live provider with **no provider override** (so the
real engine constructs the real provider from the request). Asserts the **gate behavior** — a
write paused, fired only on approve, ``cost.total_usd > 0`` — not exact model wording (§12/§14).
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from relay import assert_no_unapproved_writes
from relay.gate import STATE_CHANGE_TOOLS
from relay_api.app import create_app
from relay_api.runs import RunStore

EXAMPLES = {
    "anthropic": ("ANTHROPIC_API_KEY", None),
    "openai": ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"),
}

BILLING = (
    "Hi — I was charged twice for my Pro subscription this month (order #A-4471). "
    "Please refund the duplicate charge. — jane@acme.com"
)


def _has_key(provider: str) -> bool:
    primary, alt = EXAMPLES[provider]
    if os.environ.get(primary):
        return True
    return bool(alt and os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get(alt))


@pytest.mark.api
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_t8_real_round_trip(provider: str, tmp_path) -> None:
    if not _has_key(provider):
        pytest.skip(f"no key for provider {provider!r}")

    store = RunStore(base_dir=str(tmp_path / "runs"))
    client = TestClient(create_app(store=store))

    handle = client.post(
        "/handle", json={"ticket": BILLING, "provider": provider, "policy": "strict"}
    ).json()
    assert handle["status"] == "awaiting_approval", handle
    assert handle["cost"]["total_usd"] > 0

    run_id = handle["run_id"]
    # No state-change write fired on /handle.
    conn = store.open_db(run_id)
    try:
        written = [
            r["tool"]
            for r in conn.execute(
                "SELECT tool FROM tool_calls WHERE run_id = ?", (run_id,)
            ).fetchall()
            if r["tool"] in STATE_CHANGE_TOOLS
        ]
        assert written == []
        assert_no_unapproved_writes(run_id, conn)
    finally:
        conn.close()

    # Approve all pending → the write fires.
    decisions = [{"approval_id": p["id"], "decision": "allow"} for p in handle["actions_pending"]]
    approve = client.post("/approve", json={"run_id": run_id, "decisions": decisions}).json()
    assert approve["status"] in ("done", "awaiting_approval")  # a later turn may pause again
    assert approve["cost"]["total_usd"] >= handle["cost"]["total_usd"]

    conn = store.open_db(run_id)
    try:
        assert_no_unapproved_writes(run_id, conn)
    finally:
        conn.close()
