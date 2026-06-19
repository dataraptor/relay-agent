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

from ..models import Triage
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
        A queue of objects returned (in order) by ``structured_output`` — use for faithfulness
        (Split 04) or multi-call scripts. Takes precedence over ``triage_result``.
    steps:
        A queue of ``ModelStep`` s returned (in order) by ``step``. When exhausted, ``step``
        returns a default end-of-turn ``ModelStep`` (no tool calls) so loops terminate.
    usage:
        The ``Usage`` reported for ``structured_output`` (and for the default end-turn step).
    """

    def __init__(
        self,
        *,
        triage_result: Triage | None = None,
        structured_results: list[BaseModel] | None = None,
        steps: list[ModelStep] | None = None,
        model: str = "stub-model",
        usage: Usage | None = None,
    ) -> None:
        self.provider = "stub"
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
        if self._structured:
            result = self._structured.pop(0)
        elif self._triage_result is not None and schema_model is Triage:
            result = self._triage_result
        else:
            raise LookupError(
                f"StubProvider has no scripted structured result for {schema_model.__name__}"
            )
        return result, self._usage

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
