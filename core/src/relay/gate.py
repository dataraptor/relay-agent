"""The deterministic approval gate (spec §8) — the safety contract.

:func:`Gate.classify` is **pure code** over :data:`relay.tools.TOOL_CLASS` (a code
constant) and a per-tool policy map. The model never influences the decision: there is no
path by which model output downgrades a ``state_change`` tool out of ``ask``/``deny``
(*monotonic safety*). This is the property the never-acts-without-approval invariant rests on.

Three presets are exposed (§22-C):
  - ``default`` — ``send_reply``/``update_ticket`` ask; ``route_ticket``/``escalate`` auto.
  - ``auto``    — every state-change → ``auto``.
  - ``strict``  — every state-change → ``ask``.
Per-tool ``deny`` overrides hard-block a tool regardless of preset.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from .models import ToolClass
from .tools import TOOL_CLASS

__all__ = [
    "Policy",
    "GateAction",
    "GateDecision",
    "Gate",
    "build_policy",
    "PRESETS",
    "STATE_CHANGE_TOOLS",
]

Policy = Literal["auto", "ask", "deny"]

#: Names of the state-changing tools, derived from the code constant ``TOOL_CLASS`` (§7).
STATE_CHANGE_TOOLS: tuple[str, ...] = tuple(
    name for name, cls in TOOL_CLASS.items() if cls == ToolClass.state_change
)


class GateAction(StrEnum):
    """The deterministic outcome of classifying one proposed tool call (§8)."""

    EXECUTE = "EXECUTE"  # read/read_class, or a state_change with policy=auto
    PAUSE = "PAUSE"  # state_change with policy=ask → ApprovalRequest
    BLOCK = "BLOCK"  # state_change with policy=deny → is_error tool_result


# Default posture (Appendix A / §22-C "Decision: ship `default`").
_DEFAULT_POLICY: dict[str, Policy] = {
    "send_reply": "ask",
    "update_ticket": "ask",
    "route_ticket": "auto",
    "escalate": "auto",
}


def _uniform(value: Policy) -> dict[str, Policy]:
    return {name: value for name in STATE_CHANGE_TOOLS}


#: The three configurable presets the demo exposes.
PRESETS: dict[str, dict[str, Policy]] = {
    "default": dict(_DEFAULT_POLICY),
    "auto": _uniform("auto"),
    "strict": _uniform("ask"),
}


def build_policy(
    preset: str = "default", overrides: dict[str, str] | None = None
) -> dict[str, Policy]:
    """Return a ``{tool_name -> policy}`` map for ``preset`` with optional per-tool overrides.

    ``overrides`` lets a caller pin a single tool (e.g. ``{"escalate": "deny"}``) on top of a
    preset — used to exercise the ``deny`` path. Raises ``ValueError`` on an unknown preset or
    an invalid policy value.
    """
    if preset not in PRESETS:
        raise ValueError(f"unknown policy preset {preset!r}; choose one of {sorted(PRESETS)}")
    policy: dict[str, Policy] = dict(PRESETS[preset])
    for tool, value in (overrides or {}).items():
        if value not in ("auto", "ask", "deny"):
            raise ValueError(f"invalid policy {value!r} for {tool!r}; expected auto|ask|deny")
        policy[tool] = value  # type: ignore[assignment]
    return policy


@dataclass(frozen=True)
class GateDecision:
    """The classification of one proposed tool call. ``policy`` is ``None`` for read tools."""

    tool: str
    cls: ToolClass
    action: GateAction
    policy: Policy | None


class Gate:
    """Holds a policy map and classifies proposed tool calls deterministically (§8)."""

    def __init__(self, policy: dict[str, Policy] | None = None) -> None:
        self.policy: dict[str, Policy] = policy if policy is not None else build_policy("default")

    def classify(self, tool_name: str) -> GateDecision:
        """Classify ``tool_name`` into EXECUTE / PAUSE / BLOCK — keyed only by ``TOOL_CLASS``
        (code) and the policy map, never by anything the model emitted.

        - ``read`` / ``read_class`` → **EXECUTE** (always; no policy entry consulted).
        - ``state_change`` → policy lookup: ``auto`` → EXECUTE, ``ask`` → PAUSE, ``deny`` → BLOCK
          (default ``ask`` for any unlisted state-change tool — *bias toward pausing*).
        - unknown tool → **BLOCK** (never execute something the gate cannot classify).
        """
        cls = TOOL_CLASS.get(tool_name)
        if cls is None:
            return GateDecision(tool_name, ToolClass.state_change, GateAction.BLOCK, "deny")
        if cls in (ToolClass.read, ToolClass.read_class):
            return GateDecision(tool_name, cls, GateAction.EXECUTE, None)
        # state_change: the gate's whole reason for being.
        pol: Policy = self.policy.get(tool_name, "ask")
        if pol == "auto":
            return GateDecision(tool_name, cls, GateAction.EXECUTE, "auto")
        if pol == "deny":
            return GateDecision(tool_name, cls, GateAction.BLOCK, "deny")
        return GateDecision(tool_name, cls, GateAction.PAUSE, "ask")
