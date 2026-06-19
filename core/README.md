# core

The standalone engine — the core domain logic as a framework-free, installable package.

This package knows nothing about HTTP, UI, or how it's deployed. It exposes a clean
Python API (and a CLI) that everything else builds on. It can be imported into a
script, a notebook, the API layer, or the evaluation harness without any server running.

**Contains:** the package source (`src/`), its `pyproject.toml`, and unit tests.

**Depends on:** nothing else in this repo. This is the bottom of the stack.
