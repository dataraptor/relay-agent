#!/usr/bin/env python3
"""Relay — the one-command demo host (Split 10 R1).

Brings the whole stack up as a single same-origin demo: the gated engine + the FastAPI adapter +
the static frontend, on one port. Cross-platform (Windows / macOS / Linux):

    python demo.py                 # live demo on whatever provider key is in .env
    python demo.py --stub          # offline demo: canned data, no key, no network
    python demo.py --port 9000     # pick a port (default 8000)

What it does for a clean cold start:
  * loads ``.env`` (so ANTHROPIC_API_KEY / OpenAI / Azure creds are picked up),
  * wipes the per-run store dir (no leftover ``run_id`` / records bleed between demos),
  * points the static mount + examples at this repo's ``app/`` and ``core/examples/``,
  * prints the URL, the run mode, and which providers are available.

Keys (set any in ``.env`` or the environment):
  * ``ANTHROPIC_API_KEY``            → the ``anthropic`` provider (Claude).
  * ``OPENAI_API_KEY``               → the ``openai`` provider (api.openai.com), **or**
    ``AZURE_OPENAI_ENDPOINT`` + ``AZURE_OPENAI_API_KEY`` → the Azure gpt-5.5 deployment.

Graceful no-key behaviour (R1): the demo always loads. With a key, runs are live. With **no** key
it shows the honest missing-key banner (the "set a key" state) — unless you pass ``--stub``, which
serves the deterministic offline StubProvider path (the same one the tests/e2e use): the full
money moment on canned data, labelled ``stub`` on ``/health`` so it is never mistaken for a live run.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
APP_DIR = REPO / "app"
EXAMPLES_DIR = REPO / "core" / "examples"


def _load_env() -> None:
    """Minimal ``.env`` loader (no dependency); never overwrites an already-set var."""
    path = REPO / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _providers_available() -> dict[str, bool]:
    anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    openai = bool(os.environ.get("OPENAI_API_KEY")) or bool(
        os.environ.get("AZURE_OPENAI_ENDPOINT")
        and os.environ.get("AZURE_OPENAI_API_KEY")
    )
    return {"anthropic": anthropic, "openai": openai}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Relay one-command demo host.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument(
        "--stub",
        action="store_true",
        help="offline demo: deterministic canned provider, no key/network required.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="uvicorn auto-reload (dev only; one worker).",
    )
    args = parser.parse_args(argv)

    _load_env()

    # Clean cold start: a fresh, empty per-run store every launch (no leftover state, T3/R5).
    store_dir = os.environ.get("RELAY_API_STORE_DIR") or os.path.join(
        tempfile.gettempdir(), "relay-demo-runs"
    )
    shutil.rmtree(store_dir, ignore_errors=True)
    os.makedirs(os.path.join(store_dir, "runs"), exist_ok=True)
    os.environ["RELAY_API_STORE_DIR"] = store_dir

    # Point the API at this repo's frontend + examples (works for editable or non-editable installs).
    os.environ.setdefault("RELAY_APP_DIR", str(APP_DIR))
    os.environ.setdefault("RELAY_EXAMPLES_DIR", str(EXAMPLES_DIR))

    if args.stub:
        os.environ["RELAY_STUB"] = "1"

    # Stdout is ASCII-only (Windows cp1252 consoles raise on fancy glyphs — §20 "never crash").

    try:
        import uvicorn  # noqa: F401  (import-time check for a clear error)
        import relay_api.app  # noqa: F401
    except (
        ImportError
    ) as exc:  # pragma: no cover - install guidance, not a code path under test
        print(
            f"error: {exc}\n\nInstall the stack first (from the repo root):\n"
            '  pip install -e "core/[providers]"\n'
            "  pip install -e api/",
            file=sys.stderr,
        )
        return 1

    available = _providers_available()
    stub = bool(args.stub)
    if stub:
        mode = "OFFLINE STUB (canned data - no model call; labelled `stub` on /health)"
    elif any(available.values()):
        live = [p for p, ok in available.items() if ok]
        mode = f"LIVE on provider(s): {', '.join(live)}"
    else:
        mode = (
            "NO KEY - the demo loads and shows the missing-key banner.\n"
            "         Add a key to .env, or run `python demo.py --stub` for the offline demo."
        )

    url = f"http://{args.host}:{args.port}/"
    bar = "=" * 68
    print(
        f"\n{bar}\n  Relay demo - {url}\n  Mode: {mode}\n"
        f"  Providers available: anthropic={available['anthropic']} openai={available['openai']}\n"
        f"  Run store (fresh): {store_dir}\n"
        f"  API docs: http://{args.host}:{args.port}/docs\n{bar}\n",
        flush=True,
    )

    import uvicorn

    uvicorn.run(
        "relay_api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
