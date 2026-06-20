# core

The standalone engine — the core domain logic as a framework-free, installable package.

This package knows nothing about HTTP, UI, or how it's deployed. It exposes a clean
Python API (and a CLI) that everything else builds on. It can be imported into a
script, a notebook, the API layer, or the evaluation harness without any server running.

**Contains:** the package source (`src/`), its `pyproject.toml`, and unit tests.

**Depends on:** nothing else in this repo. This is the bottom of the stack.

## What's inside

`models.py` (Triage + tool I/O + Outcome) · `prompts.py` · `cost.py` (cache-aware, per-provider) ·
`provider/` (the `ProviderClient` seam — `anthropic.py`, `openai.py`, `stub.py`) · `tools.py` (the 7
tools + declared class) · `gate.py` (the deterministic approval gate) · `agent.py` (`handle()` /
`approve()` — the manual tool-use loop) · `faithfulness.py` · `backend/` (mock SQLite + seed).

## Run / test

```bash
pip install -e "core/[providers]"             # editable install with the provider SDKs
python -m relay.cli handle --example core/examples/billing_dispute.json --policy strict
python -m pytest -m "not api"                  # Tier-1: no key, deterministic (the safety invariant)
python -m pytest -m api                        # Tier-2: live provider round-trip (needs a key; auto-skipped)
```

For the project story, the architecture, and the leaderboard, start at the [root README](../README.md).
