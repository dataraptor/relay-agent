"""Gate classification matrix (T1) and monotonic safety (T2) — a CI hard gate (E7).

These run with no API key. Every tool × every preset × the ``deny`` override is asserted to
the exact decision; the model never gets a say (``classify`` takes only a tool name).
"""

from __future__ import annotations

import pytest

from relay.gate import (
    PRESETS,
    STATE_CHANGE_TOOLS,
    Gate,
    GateAction,
    build_policy,
)
from relay.models import ToolClass
from relay.tools import TOOL_CLASS

READ_TOOLS = [n for n, c in TOOL_CLASS.items() if c in (ToolClass.read, ToolClass.read_class)]

# Expected gate action per (preset, state_change tool). Read tools always EXECUTE.
_EXPECTED = {
    "default": {
        "send_reply": GateAction.PAUSE,
        "update_ticket": GateAction.PAUSE,
        "route_ticket": GateAction.EXECUTE,
        "escalate": GateAction.EXECUTE,
    },
    "auto": {name: GateAction.EXECUTE for name in STATE_CHANGE_TOOLS},
    "strict": {name: GateAction.PAUSE for name in STATE_CHANGE_TOOLS},
}


def test_state_change_set_is_exactly_the_four_writes() -> None:
    assert set(STATE_CHANGE_TOOLS) == {"send_reply", "update_ticket", "route_ticket", "escalate"}


@pytest.mark.parametrize("preset", ["default", "auto", "strict"])
@pytest.mark.parametrize("tool", READ_TOOLS)
def test_reads_always_execute(preset: str, tool: str) -> None:
    """Reads / read-class execute under every preset (no policy entry consulted)."""
    decision = Gate(build_policy(preset)).classify(tool)
    assert decision.action == GateAction.EXECUTE
    assert decision.policy is None  # reads never consult the policy map


@pytest.mark.parametrize("preset", ["default", "auto", "strict"])
@pytest.mark.parametrize("tool", list(STATE_CHANGE_TOOLS))
def test_state_change_matrix(preset: str, tool: str) -> None:
    """The exact EXECUTE/PAUSE decision for every state-change tool under every preset (T1)."""
    decision = Gate(build_policy(preset)).classify(tool)
    assert decision.action == _EXPECTED[preset][tool]
    assert decision.cls == ToolClass.state_change


@pytest.mark.parametrize("preset", ["default", "auto", "strict"])
@pytest.mark.parametrize("tool", list(STATE_CHANGE_TOOLS))
def test_deny_override_blocks_in_every_preset(preset: str, tool: str) -> None:
    """A ``deny`` override hard-blocks a state-change regardless of the underlying preset (T1)."""
    gate = Gate(build_policy(preset, overrides={tool: "deny"}))
    decision = gate.classify(tool)
    assert decision.action == GateAction.BLOCK
    assert decision.policy == "deny"


def test_unknown_tool_is_blocked() -> None:
    """A tool the gate cannot classify is BLOCKed — never executed (monotonic safety)."""
    decision = Gate(build_policy("auto")).classify("definitely_not_a_tool")
    assert decision.action == GateAction.BLOCK


def test_unlisted_state_change_defaults_to_ask() -> None:
    """Bias toward pausing: a state-change with no policy entry defaults to PAUSE, not EXECUTE."""
    # An empty policy map: update_ticket has no entry → default ask → PAUSE.
    decision = Gate({}).classify("update_ticket")
    assert decision.action == GateAction.PAUSE


def test_default_gate_uses_default_preset() -> None:
    assert Gate().policy == PRESETS["default"]


# --- T2: monotonic safety -----------------------------------------------------


def test_classify_ignores_everything_but_tool_name_and_policy() -> None:
    """``classify`` has a single argument — the tool name. There is no parameter through which
    model output could influence the decision (the structural half of monotonic safety)."""
    import inspect

    params = list(inspect.signature(Gate.classify).parameters)
    assert params == ["self", "tool_name"]


def test_no_preset_downgrades_ask_or_deny_to_execute() -> None:
    """A state-change that is ``ask`` or ``deny`` can never resolve to EXECUTE — across every
    preset and every deny override (T2)."""
    for preset in PRESETS:
        for tool in STATE_CHANGE_TOOLS:
            # strict and deny must never EXECUTE.
            assert Gate(build_policy("strict")).classify(tool).action != GateAction.EXECUTE
            assert (
                Gate(build_policy(preset, overrides={tool: "deny"})).classify(tool).action
                == GateAction.BLOCK
            )


def test_build_policy_rejects_bad_preset_and_value() -> None:
    with pytest.raises(ValueError):
        build_policy("nonsense")
    with pytest.raises(ValueError):
        build_policy("default", overrides={"escalate": "maybe"})
