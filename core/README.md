# core: the standalone engine

The core domain logic as a framework-free, installable package.

This package knows nothing about HTTP, UI, or how it's deployed. It exposes a clean Python API (and a CLI) that everything else builds on. You can import it into a script, a notebook, the API layer, or the evaluation harness without any server running.

**Contains:** the package source (`src/`), its `pyproject.toml`, and unit tests.

**Depends on:** nothing else in this repo. This is the bottom of the stack.

## What's inside

- `models.py`: Triage, tool I/O, and Outcome types
- `prompts.py`: the prompt templates
- `cost.py`: cache-aware, per-provider cost accounting
- `provider/`: the `ProviderClient` seam (`anthropic.py`, `openai.py`, `stub.py`)
- `tools.py`: the 7 tools and their declared classes
- `gate.py`: the deterministic approval gate
- `agent.py`: `handle()` and `approve()`, the manual tool-use loop
- `faithfulness.py`: the grounded-reply check
- `backend/`: the mock SQLite backend and seed data

## Run / test

```bash
pip install -e "core/[providers]"             # editable install with the provider SDKs
python -m relay.cli handle --example core/examples/billing_dispute.json --policy strict
python -m pytest -m "not api"                 # Tier-1: no key, deterministic (the safety invariant)
python -m pytest -m api                        # Tier-2: live provider round-trip (needs a key; auto-skipped)
```

For the project story, the architecture, and the leaderboard, start at the [root README](../README.md).
</content>
