# Relay — developer + demo entrypoints (Split 10 R1).
# `make demo` is the one-command host; on Windows (no make) run `python demo.py` directly.
.PHONY: demo demo-stub install test e2e fmt

# Live demo on whatever provider key is in .env (falls back to a missing-key banner if none).
demo:
	python demo.py

# Offline demo: deterministic canned data, no key or network required.
demo-stub:
	python demo.py --stub

# Install the whole stack (engine + provider SDKs + HTTP adapter), editable.
install:
	pip install -e "core/[providers]"
	pip install -e api/

# All Tier-1 (no-key) suites: engine, API, eval deterministic tier, frontend.
test:
	cd core && python -m pytest -q -m "not api"
	cd api  && python -m pytest -q -m "not api"
	cd eval && python -m pytest tests -q -m "not api"
	cd app  && npm test

# The cross-stack end-to-end proof (boots a real server; stub path needs no key).
e2e:
	cd app && npm run e2e

fmt:
	cd core && python -m ruff format . && python -m ruff check --fix .
	cd api  && python -m ruff format . && python -m ruff check --fix .
	cd eval && python -m ruff format . && python -m ruff check --fix .
