"""Package sanity: clean import + the CLI is wired (Split 03 makes handle/approve/seed real)."""

from __future__ import annotations

import pytest

import relay
from relay import cli


def test_version_exposed() -> None:
    assert relay.__version__ == "0.1.0"


def test_cli_requires_a_subcommand(capsys) -> None:
    # argparse rejects an empty invocation (a subcommand is now required) with exit code 2.
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
