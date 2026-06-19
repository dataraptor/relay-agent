"""Package sanity: clean import + the Split-04 CLI stub behaves (prints, exits non-zero)."""

from __future__ import annotations

import relay
from relay import cli


def test_version_exposed() -> None:
    assert relay.__version__ == "0.1.0"


def test_cli_stub_returns_nonzero(capsys) -> None:
    rc = cli.main([])
    assert rc == 1
    assert "not yet implemented" in capsys.readouterr().err
