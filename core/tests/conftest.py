"""Test setup: make API keys from a local ``.env`` available to the Tier-2 (``@api``) tests.

Tier-1 (no-key) tests never read these — they stub the provider. But the ``@api`` tests skip
unless a real key is present, so this loads a ``.env`` (from ``core/`` or the repo root) into
``os.environ`` **without overwriting** anything already set. No third-party dependency: a tiny
``KEY=VALUE`` parser is enough. Keys are never printed or committed (``.env`` is gitignored).
"""

from __future__ import annotations

import os
from pathlib import Path


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


# Look in core/ and the repo root (core/..). First found wins per key (no overwrite).
_here = Path(__file__).resolve()
for candidate in (_here.parents[1] / ".env", _here.parents[2] / ".env"):
    _load_env_file(candidate)
