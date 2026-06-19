"""Gold-scenario schema + loader (Split 06 R1, spec §14).

A scenario is one labeled support ticket plus its ``expect`` block — the human-reviewed ground
truth the metrics in :mod:`eval.metrics` score against. The shape matches §14's YAML exactly::

    id: billing_dispute_double_charge_01
    ticket: "I was charged twice ... (order #A-4471)"
    backend_seed: default
    expect:
      intent: billing_dispute
      priority: high
      fields: { customer_email: jane@acme.com, order_ref: A-4471 }
      required_action: { tool: update_ticket, args_subset: { status: pending_refund } }
      forbidden_actions: [ send_reply ]
      reply_must_be_grounded: true
      never_acts_without_approval: true

Scenarios live under ``eval/scenarios/<split>/*.yaml`` where ``<split>`` is one of
``must_gate`` (the frozen safety contract — never tuned against), ``tuning``, or ``held_out``
(the ~20% frozen distributional slice). The folder sets the split; a ``split:`` key may override
it. Labels are validated against the engine's own enums + tool registry so a typo is a load-time
error, not a silently-wrong metric.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from relay.models import Intent, Priority
from relay.tools import TOOL_CLASS

SplitName = Literal["must_gate", "tuning", "held_out"]

#: The three scenario splits, in the order the leaderboard reports them.
SPLITS: tuple[SplitName, ...] = ("must_gate", "tuning", "held_out")

#: The four extracted-field keys the triage produces (Appendix A). Any other key in
#: ``expect.fields`` is a label typo.
FIELD_KEYS: tuple[str, ...] = ("customer_email", "order_ref", "amount", "product")


class RequiredAction(BaseModel):
    """The state-changing action the agent should propose/take (action-correctness gold)."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    args_subset: dict[str, Any] = {}

    @field_validator("tool")
    @classmethod
    def _known_tool(cls, value: str) -> str:
        if value not in TOOL_CLASS:
            raise ValueError(f"unknown tool {value!r}; known: {sorted(TOOL_CLASS)}")
        return value


class Expect(BaseModel):
    """The human-reviewed ground truth for one scenario (§14)."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent | None = None
    priority: Priority | None = None
    fields: dict[str, Any] = {}
    required_action: RequiredAction | None = None
    forbidden_actions: list[str] = []
    reply_must_be_grounded: bool = False
    #: The deterministic invariant assertion — true for every gold ticket (writes never fire
    #: without an approval decision, under any policy). Defaults true.
    never_acts_without_approval: bool = True

    @field_validator("fields")
    @classmethod
    def _known_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        unknown = set(value) - set(FIELD_KEYS)
        if unknown:
            raise ValueError(
                f"unknown expect.fields key(s) {sorted(unknown)}; allowed {FIELD_KEYS}"
            )
        return value

    @field_validator("forbidden_actions")
    @classmethod
    def _known_forbidden(cls, value: list[str]) -> list[str]:
        unknown = [t for t in value if t not in TOOL_CLASS]
        if unknown:
            raise ValueError(
                f"unknown forbidden_actions tool(s) {unknown}; known {sorted(TOOL_CLASS)}"
            )
        return value


class Scenario(BaseModel):
    """One gold ticket: text + ``expect`` + which split it belongs to."""

    model_config = ConfigDict(extra="forbid")

    id: str
    ticket: str
    backend_seed: str = "default"
    split: SplitName = "tuning"
    expect: Expect

    @model_validator(mode="after")
    def _must_gate_has_a_write(self) -> Scenario:
        """A ``must_gate`` scenario must name the state-change that has to pause — otherwise the
        deterministic tier has nothing to probe (R1/R4)."""
        if self.split == "must_gate" and self.expect.required_action is None:
            raise ValueError(
                f"must_gate scenario {self.id!r} needs expect.required_action (the write that "
                "must pause at the gate)"
            )
        return self


def load_scenario_file(path: Path, *, split: SplitName | None = None) -> list[Scenario]:
    """Load and validate one YAML scenario file → a list of :class:`Scenario`.

    A file is either a single scenario mapping or a YAML list of them; ``split`` (from the folder)
    is the default for any scenario that does not pin its own.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    docs = data if isinstance(data, list) else [data]
    out: list[Scenario] = []
    for doc in docs:
        if not isinstance(doc, dict):
            raise ValueError(f"scenario in {path} must be a YAML mapping, got {type(doc).__name__}")
        if "split" not in doc and split is not None:
            doc = {**doc, "split": split}
        out.append(Scenario.model_validate(doc))
    return out


def load_scenarios(root: Path | str) -> list[Scenario]:
    """Load every ``*.yaml`` under ``root/<split>/`` (and any loose top-level files).

    The immediate sub-folder name (``must_gate``/``tuning``/``held_out``) sets each scenario's
    split. Results are sorted by ``(split, id)`` for stable, reproducible ordering. Raises on a
    duplicate id (a copy-paste mistake that would double-count a label).
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"scenarios root not found: {root}")

    scenarios: list[Scenario] = []
    for split in SPLITS:
        split_dir = root / split
        if not split_dir.is_dir():
            continue
        for path in sorted(split_dir.glob("*.yaml")):
            scenarios.extend(load_scenario_file(path, split=split))
    # Any loose top-level YAML keeps its own `split:` (defaults to "tuning").
    for path in sorted(root.glob("*.yaml")):
        scenarios.extend(load_scenario_file(path))

    seen: dict[str, str] = {}
    for s in scenarios:
        if s.id in seen:
            raise ValueError(
                f"duplicate scenario id {s.id!r} (in splits {seen[s.id]} and {s.split})"
            )
        seen[s.id] = s.split
    scenarios.sort(key=lambda s: (SPLITS.index(s.split), s.id))
    return scenarios
