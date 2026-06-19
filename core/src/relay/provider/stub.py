"""``StubProvider`` — a no-network ``ProviderClient`` driven by canned results.

This is the engine that makes the **never-acts-without-approval** invariant testable for free
(Splits 03/06): script a sequence of ``ModelStep`` s ("turn 1 proposes ``lookup_customer``,
turn 2 proposes ``update_ticket``, turn 3 ends") and run the real loop against it with zero API
calls. It lives in the **installed package** (not ``tests/``) because Split 06 (eval) and Split 07
(FastAPI dependency-override) import it across layer boundaries — they depend on ``core`` but
cannot import ``core/tests/`` (R4).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..models import Faithfulness, Triage
from ..prompts import TRIAGE_SYSTEM, triage_user_content
from .base import ModelStep, Usage


class StubProvider:
    """Plays scripted structured-output results and ``ModelStep`` s; records every call.

    Parameters
    ----------
    triage_result:
        Returned by ``triage`` / ``structured_output(..., Triage)`` when no explicit
        ``structured_results`` are queued. A convenience for the common single-triage case.
    structured_results:
        A queue of objects returned by ``structured_output`` — use for faithfulness (Split 04)
        or multi-call scripts. Dispatch is **schema-matched**: a call pops the first queued item
        of the requested type, so a triage call and a faithfulness call in the same run don't
        collide (the triage call won't consume a queued ``Faithfulness``, and vice versa).
    steps:
        A queue of ``ModelStep`` s returned (in order) by ``step``. When exhausted, ``step``
        returns a default end-of-turn ``ModelStep`` (no tool calls) so loops terminate.
    usage:
        The ``Usage`` reported for ``structured_output`` (and for the default end-turn step).
    provider:
        The provider name reported (default ``"stub"``). Set to a real provider such as
        ``"anthropic"`` (with a priced ``model``) when a test needs the loop's cost ledger to
        accumulate non-zero ``cost_usd`` from canned usage (Split 03 T8).
    """

    def __init__(
        self,
        *,
        triage_result: Triage | None = None,
        structured_results: list[BaseModel] | None = None,
        steps: list[ModelStep] | None = None,
        model: str = "stub-model",
        usage: Usage | None = None,
        provider: str = "stub",
    ) -> None:
        self.provider = provider
        self.model = model
        self._triage_result = triage_result
        self._structured = list(structured_results or [])
        self._steps = list(steps or [])
        self._step_index = 0
        self._usage = usage or Usage(input_tokens=10, output_tokens=5)
        #: Append-only record of (method, *args) for assertions.
        self.calls: list[tuple[Any, ...]] = []

    def structured_output(
        self, system: str, user: str, schema_model: type[BaseModel]
    ) -> tuple[BaseModel, Usage]:
        self.calls.append(("structured_output", system, user, schema_model))
        # 1. A queued result of the requested type wins (scripted multi-call: triage + faithful).
        for i, item in enumerate(self._structured):
            if isinstance(item, schema_model):
                return self._structured.pop(i), self._usage
        # 2. The triage_result convenience for the common single-triage case.
        if schema_model is Triage and self._triage_result is not None:
            return self._triage_result, self._usage
        # 3. A graceful all-grounded default so loop scripts with draft_reply needn't script the
        #    faithfulness verdict when they don't care about it (mirrors step()'s default end-turn).
        if issubclass(schema_model, Faithfulness):
            return Faithfulness(all_grounded=True, claims=[]), self._usage
        raise LookupError(
            f"StubProvider has no scripted structured result for {schema_model.__name__}"
        )

    def triage(self, ticket: str) -> Triage:
        result, _ = self.structured_output(TRIAGE_SYSTEM, triage_user_content(ticket), Triage)
        assert isinstance(result, Triage)
        return result

    def step(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelStep:
        self.calls.append(("step", messages, tools))
        if self._step_index >= len(self._steps):
            # No more scripted turns → end the loop cleanly.
            return ModelStep(text="", tool_calls=[], usage=self._usage, stop_reason="end_turn")
        step = self._steps[self._step_index]
        self._step_index += 1
        return step
