"""T3 / E4 / E5 — schema sanity, contract fidelity, and the strict-mode nullable contract."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from relay import models as M

_FORBIDDEN_KEYWORDS = {
    "minLength",
    "maxLength",
    "maximum",
    "minimum",
    "exclusiveMaximum",
    "exclusiveMinimum",
}


def _iter_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_nodes(item)


def _all_keys(schema: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for node in _iter_nodes(schema):
        keys.update(node.keys())
    return keys


def _object_nodes(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in _iter_nodes(schema) if n.get("type") == "object" and "properties" in n]


def _permits_null(prop: dict[str, Any]) -> bool:
    if prop.get("type") == "null":
        return True
    if isinstance(prop.get("type"), list) and "null" in prop["type"]:
        return True
    return any(
        sub.get("type") == "null"
        for sub in prop.get("anyOf", []) + prop.get("oneOf", [])
        if isinstance(sub, dict)
    )


# --- E4: contract fidelity (enum members + field names match spec verbatim) ---


def test_enum_members_match_spec_verbatim() -> None:
    assert {e.value for e in M.Intent} == {
        "billing_dispute",
        "refund_request",
        "technical_issue",
        "account_access",
        "feature_request",
        "abuse_report",
        "general_question",
        "spam",
    }
    assert {e.value for e in M.Priority} == {"low", "normal", "high", "urgent"}
    assert {e.value for e in M.Confidence} == {"high", "medium", "low"}
    assert {e.value for e in M.Decision} == {"auto", "approved", "rejected", "blocked"}
    assert {e.value for e in M.ToolClass} == {"read", "read_class", "state_change"}
    assert M.TOOL_CLASS_LABELS[M.ToolClass.read_class] == "read-class"


def test_extracted_fields_names_are_exact() -> None:
    assert list(M.ExtractedFields.model_fields.keys()) == [
        "customer_email",
        "order_ref",
        "amount",
        "product",
    ]


def test_triage_field_names_are_exact() -> None:
    assert list(M.Triage.model_fields.keys()) == [
        "intent",
        "priority",
        "extracted_fields",
        "confidence",
    ]


# --- T3 / E5: schema sanity ---


def test_triage_schema_has_no_forbidden_keywords() -> None:
    for schema in (M.Triage.model_json_schema(), M.strict_json_schema(M.Triage)):
        assert _all_keys(schema).isdisjoint(_FORBIDDEN_KEYWORDS)


def test_triage_objects_forbid_additional_properties() -> None:
    schema = M.strict_json_schema(M.Triage)
    objects = _object_nodes(schema)
    assert objects, "expected at least the Triage + ExtractedFields object nodes"
    assert all(n.get("additionalProperties") is False for n in objects)


def test_no_recursive_ref() -> None:
    # No self-referential $ref: a $ref must point at a *different* def than the one it sits in.
    schema = M.Triage.model_json_schema()
    defs = schema.get("$defs", {})
    for name, body in defs.items():
        refs = [n["$ref"] for n in _iter_nodes(body) if "$ref" in n]
        assert f"#/$defs/{name}" not in refs


def test_extracted_fields_required_but_nullable_strict_contract() -> None:
    # The Split-05 strict-mode contract: all keys present in `required`, each a null-union.
    schema = M.strict_json_schema(M.Triage)
    ef = schema["$defs"]["ExtractedFields"]
    assert set(ef["required"]) == {"customer_email", "order_ref", "amount", "product"}
    assert ef["additionalProperties"] is False
    for name, prop in ef["properties"].items():
        assert _permits_null(prop), f"{name} must permit null"


def test_strict_schema_strips_defaults() -> None:
    # OpenAI strict mode rejects `default`; the strict transform must remove every one.
    assert "default" not in _all_keys(M.strict_json_schema(M.Triage))


# --- Round-trips ---


def test_triage_round_trip() -> None:
    t = M.Triage(
        intent="billing_dispute",
        priority="high",
        extracted_fields=M.ExtractedFields(customer_email="jane@acme.com", order_ref="A-4471"),
        confidence="high",
    )
    dumped = t.model_dump()
    assert dumped["extracted_fields"]["amount"] is None
    again = M.Triage.model_validate(dumped)
    assert again == t


def test_extracted_fields_default_all_none() -> None:
    ef = M.ExtractedFields()
    assert ef.customer_email is None and ef.amount is None


def test_all_seven_tools_have_io_models() -> None:
    for tool in (
        "LookupCustomer",
        "SearchKb",
        "DraftReply",
        "SendReply",
        "UpdateTicket",
        "RouteTicket",
        "Escalate",
    ):
        assert hasattr(M, f"{tool}Input")
        assert hasattr(M, f"{tool}Output")


def test_search_kb_output_round_trip() -> None:
    out = M.SearchKbOutput(
        results=[M.KbHit(chunk_id="kb-1", text="t", source="s", url="u", score=1.2)]
    )
    assert M.SearchKbOutput.model_validate(out.model_dump()) == out


def test_outcome_id_equals_run_id() -> None:
    triage = M.Triage(
        intent="spam", priority="low", extracted_fields=M.ExtractedFields(), confidence="low"
    )
    o = M.Outcome(
        id="abc",
        triage=triage,
        status="done",
        provider="anthropic",
        model="claude-sonnet-4-6",
        prompt_version="relay-prompts-v1",
    )
    assert o.run_id == "abc"

    # Explicitly passing a matching run_id is accepted (no-op equality path).
    o2 = M.Outcome(
        id="abc",
        run_id="abc",
        triage=triage,
        status="done",
        provider="anthropic",
        model="claude-sonnet-4-6",
        prompt_version="relay-prompts-v1",
    )
    assert o2.run_id == "abc"

    with pytest.raises(ValueError):
        M.Outcome(
            id="abc",
            run_id="different",
            triage=triage,
            status="done",
            provider="anthropic",
            model="claude-sonnet-4-6",
            prompt_version="relay-prompts-v1",
        )


def test_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        M.ExtractedFields(unexpected="x")  # type: ignore[call-arg]


def test_faithfulness_and_assembled_types_construct() -> None:
    f = M.Faithfulness(all_grounded=True, claims=[M.ClaimVerdict(claim="c", label="SUPPORTED")])
    dr = M.DraftReply(
        body="hi", citations=[M.Citation(chunk_id="kb-1", source="s", url="u")], faithfulness=f
    )
    ar = M.ActionResult(tool="update_ticket", decision="approved", approver="op")
    pa = M.ProposedAction(tool="update_ticket", cls=M.ToolClass.state_change)
    apr = M.ApprovalRequest(id="a1", tool="update_ticket")
    assert dr.faithfulness.all_grounded
    assert ar.decision is M.Decision.approved
    assert pa.cls is M.ToolClass.state_change
    assert apr.id == "a1"
