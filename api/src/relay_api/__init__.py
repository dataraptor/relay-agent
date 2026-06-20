"""Relay HTTP API — a thin FastAPI adapter over the ``relay`` engine (Split 07).

The API holds **no business logic**: the gate, loop, faithfulness, and cost all stay in
``core``. Its only non-trivial code is the **run store** (cross-request suspend/resume
persistence) and the **RunView** projection (serialization of the engine's ``Outcome`` plus
presentation-derived enrichments — ordered trace, touched records, cost breakdown). The
never-acts-without-approval invariant is proven in ``core`` and survives the HTTP boundary:
a state-changing write fires only on ``POST /approve``, never on ``POST /handle``.
"""

from __future__ import annotations

from .app import create_app

__version__ = "0.1.0"

__all__ = ["create_app", "__version__"]
