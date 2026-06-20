"""Provider facts + worked examples for ``/config``, ``/health``, ``/examples`` (Split 07 R3).

**One source of truth.** Model IDs come straight from the ``relay`` provider modules (Split 05's
pinned strings) and example text from ``core/examples/`` — the API hardcodes no second copy that
could drift from the engine (R3, E5). Importing the provider modules is light: the SDK imports are
lazy (inside the provider constructors), so reading ``ANTHROPIC_MODELS`` / ``OPENAI_MODELS`` never
pulls in ``anthropic`` / ``openai``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import relay
from relay.provider import anthropic as _anthropic
from relay.provider import openai as _openai

from .schemas import ConfigResponse, ExampleTicket, HealthResponse

#: The four worked tickets (filename -> display label); injection is locked in the composer (R3).
_EXAMPLE_FILES: tuple[tuple[str, str, bool], ...] = (
    ("billing_dispute", "billing", False),
    ("tech_issue", "tech", False),
    ("ambiguous", "ambiguous", False),
    ("injection", "injection", True),
)


def _examples_dir() -> Path:
    """Locate ``core/examples`` relative to the installed (editable) ``relay`` package.

    Overridable via ``RELAY_EXAMPLES_DIR`` for non-editable deployments. The path is
    ``<repo>/core/src/relay`` → ``parents[2]`` is ``core/`` → ``core/examples``.
    """
    override = os.environ.get("RELAY_EXAMPLES_DIR")
    if override:
        return Path(override)
    return Path(relay.__file__).resolve().parents[2] / "examples"


def load_examples() -> list[ExampleTicket]:
    """The four ``core/examples`` tickets as composer entries (R3). Skips any file that is
    missing/malformed rather than crashing the endpoint (§20)."""
    out: list[ExampleTicket] = []
    base = _examples_dir()
    for stem, label, lock in _EXAMPLE_FILES:
        path = base / f"{stem}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ticket = data.get("ticket") if isinstance(data, dict) else None
        if not ticket:
            continue
        out.append(ExampleTicket(id=stem, label=label, ticket=str(ticket), lock=lock))
    return out


def stub_mode() -> bool:
    """Whether the server serves the offline/CI StubProvider path (Split 10 R1/R4).

    On when ``RELAY_STUB`` is truthy. This is an explicit opt-in: a missing key on its own does
    **not** silently fake a run — it returns the honest ``424 missing_key`` banner (R1's "clear
    'set a key' state"). The stub path is for offline reviewer demos and the deterministic
    cross-stack e2e, and is labelled ``stub: true`` on ``/health`` + ``/config``.
    """
    return os.environ.get("RELAY_STUB", "").strip().lower() in ("1", "true", "yes", "on")


def build_config() -> ConfigResponse:
    """The selectable surface, with model IDs sourced from ``core`` (R3, E5)."""
    anthropic_default = _anthropic.DEFAULT_MODEL
    anthropic_models = [anthropic_default] + sorted(
        _anthropic.ANTHROPIC_MODELS - {anthropic_default}
    )
    openai_default = _openai.DEFAULT_MODEL
    openai_models = [openai_default] + sorted(_openai.OPENAI_MODELS - {openai_default})
    return ConfigResponse(
        providers=["anthropic", "openai"],
        models_by_provider={"anthropic": anthropic_models, "openai": openai_models},
        policies=["auto", "default", "strict"],
        default_provider="anthropic",
        default_model_by_provider={"anthropic": anthropic_default, "openai": openai_default},
        stub=stub_mode(),
    )


def provider_available(provider: str) -> bool:
    """Whether ``provider``'s API key is present in the environment (drives ``/health`` + the
    frontend's missing-key banner). OpenAI counts either api.openai.com or the Azure deployment."""
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "openai":
        if os.environ.get("OPENAI_API_KEY"):
            return True
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_API_KEY")
        )
    return False


def env_var_for(provider: str) -> str:
    """The canonical env var name a missing-key envelope reports (the banner reads it). The
    Azure-vs-OpenAI hosting flavour never leaks — only the provider name (carry-forward §05)."""
    return {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider, "")


def build_health() -> HealthResponse:
    return HealthResponse(
        providers_available={
            "anthropic": provider_available("anthropic"),
            "openai": provider_available("openai"),
        },
        stub=stub_mode(),
    )
