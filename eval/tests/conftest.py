"""Test setup for the eval layer.

Two jobs: (1) guarantee the repo root is on ``sys.path`` so ``import eval.metrics`` resolves no
matter which directory pytest is invoked from (belt-and-suspenders alongside the ``pythonpath``
ini option), and (2) load a local ``.env`` so the Tier-2 (``@api``) tests can find a real provider
key — Tier-1 (no-key) tests never read it. Keys are never printed or committed (``.env`` is
gitignored). Mirrors ``core/tests/conftest.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]  # eval/tests/conftest.py -> repo root
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


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


for candidate in (_REPO_ROOT / ".env", _HERE.parents[1] / ".env"):
    _load_env_file(candidate)
