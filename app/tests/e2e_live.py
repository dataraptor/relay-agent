"""Split 08 -- T5: documented end-to-end smoke (Tier-2; needs a provider key).

Drives the **real ASGI app** (the same routes the served frontend calls) end-to-end with a real
provider, proving the money moment across HTTP: the billing ticket pauses at the gate, **no write
fires until /approve**, and the write commits only on approve. Also confirms the static frontend
(Relay.dc.html + api.js + map.js) is actually served.

Run it (from the repo root, with a key in ``.env``):

    python app/tests/e2e_live.py            # auto-picks an available provider (openai/anthropic)
    python app/tests/e2e_live.py anthropic  # force a provider

Exits 0 on PASS, 2 on a real failure, 3 if no provider key is configured (skip).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from relay.gate import STATE_CHANGE_TOOLS
from relay_api.app import create_app
from relay_api.meta import provider_available
from relay_api.runs import RunStore

BILLING = (
    "Hi -- I was charged twice for my Pro subscription this month (order #A-4471). "
    "Please refund the duplicate charge. -- jane@acme.com"
)


def _load_env() -> None:
    for candidate in (Path("app/.env"), Path("api/.env"), Path(".env")):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def _state_change_writes(store: RunStore, run_id: str) -> list[str]:
    conn = store.open_db(run_id)
    try:
        rows = conn.execute(
            "SELECT tool FROM tool_calls WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [r["tool"] for r in rows if r["tool"] in STATE_CHANGE_TOOLS]
    finally:
        conn.close()


def _ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def main() -> int:
    _load_env()
    forced = sys.argv[1] if len(sys.argv) > 1 else None
    provider = forced or next(
        (p for p in ("openai", "anthropic") if provider_available(p)), None
    )
    if provider is None or not provider_available(provider):
        print(f"SKIP -- no key for provider {provider!r}; set one in .env to run the live e2e.")
        return 3

    print(f"Live e2e on provider={provider!r} (the billing money demo over HTTP)\n")
    store = RunStore(base_dir=str(Path(os.environ.get("TEMP", "/tmp")) / "relay-e2e-runs"))
    client = TestClient(create_app(store=store))

    # --- meta the frontend loads on mount ---
    health = client.get("/health").json()
    assert health["providers_available"][provider], "provider key not seen by the API"
    _ok("GET /health -> provider available")
    config = client.get("/config").json()
    assert provider in config["providers"] and config["models_by_provider"][provider]
    _ok(f"GET /config -> models {config['models_by_provider'][provider]}")
    examples = client.get("/examples").json()
    assert any(e["label"] == "billing" for e in examples)
    _ok(f"GET /examples -> {[e['label'] for e in examples]}")

    # --- the served frontend (R1/R5) ---
    for path in ("/app/Relay.dc.html", "/app/api.js", "/app/map.js"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
    _ok("GET /app/{Relay.dc.html,api.js,map.js} -> 200 (frontend served)")
    assert client.get("/", follow_redirects=False).status_code in (200, 307)
    _ok("GET / -> redirects to the app")

    # --- handle: a write must PAUSE, nothing committed ---
    handle = client.post(
        "/handle", json={"ticket": BILLING, "provider": provider, "policy": "strict"}
    ).json()
    assert handle.get("status") == "awaiting_approval", handle
    assert handle["actions_pending"], "expected a pending action at the gate"
    assert handle["cost"]["total_usd"] > 0
    run_id = handle["run_id"]
    pending = handle["actions_pending"][0]
    _ok(
        f"POST /handle -> awaiting_approval; paused {pending['tool']!r}; "
        f"$/ticket={handle['cost']['total_usd']:.6f}"
    )

    writes = _state_change_writes(store, run_id)
    assert writes == [], f"a write fired before approval: {writes}"
    _ok("THE PAUSE IS REAL -- DB shows 0 state-change writes before /approve")

    # --- approve: the write fires only now ---
    decisions = [{"approval_id": p["id"], "decision": "allow"} for p in handle["actions_pending"]]
    approve = client.post("/approve", json={"run_id": run_id, "decisions": decisions}).json()
    assert approve["status"] in ("done", "awaiting_approval"), approve
    assert approve["cost"]["total_usd"] >= handle["cost"]["total_usd"]
    writes_after = _state_change_writes(store, run_id)
    assert writes_after, "the write did not fire on approve"
    cost_after = approve["cost"]["total_usd"]
    _ok(f"POST /approve -> write {writes_after} committed; $/ticket={cost_after:.6f}")

    # records reflect the committed write
    ticket = (approve.get("records") or {}).get("ticket") or {}
    _ok(f"records.ticket after approve -> {ticket}")

    print("\nE2E PASS -- the real gate held across HTTP: no write until Approve.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:  # a real failure, surfaced clearly
        print(f"\nE2E FAIL -- {exc}")
        sys.exit(2)
