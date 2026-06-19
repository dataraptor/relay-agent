"""Relay CLI entrypoint (stub).

The real command surface (``handle`` / ``approve`` / ``eval`` / ``seed``, spec §16) lands in
Split 04. This stub exists only so the declared console-script and ``python -m relay.cli`` are
honest in Split 01 — it prints a clear "not yet implemented" message and exits non-zero.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    print("relay CLI is not yet implemented (built in Split 04).", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover - thin entrypoint
    raise SystemExit(main())
