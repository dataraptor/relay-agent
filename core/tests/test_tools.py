"""Tool surface: schemas (T1), backend execution (T2), class map (T3, E4),
BM25 determinism (T4), grounding target (E5), clean gate seam (E6)."""

from __future__ import annotations

import json

import pytest

from relay import tools
from relay.backend import db
from relay.models import ToolClass

# Forbidden JSON-schema keywords (unsupported by Anthropic/OpenAI strict; §API conformance).
_FORBIDDEN_KEYWORDS = {
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "pattern",
    "minProperties",
    "maxProperties",
}

_EXPECTED_REQUIRED = {
    "lookup_customer": [],
    "search_kb": ["query"],
    "draft_reply": ["body"],
    "send_reply": ["to", "body"],
    "update_ticket": ["ticket_id"],
    "route_ticket": ["ticket_id", "queue"],
    "escalate": ["ticket_id", "level", "rationale"],
}


# --- T1: tool schemas --------------------------------------------------------


def test_every_tool_emits_a_valid_provider_schema() -> None:
    schemas = {s["name"]: s for s in tools.tool_schemas()}
    assert set(schemas) == set(tools.REGISTRY)
    for name, schema in schemas.items():
        assert schema["name"] == name
        assert schema["description"]
        isch = schema["input_schema"]
        assert isch["type"] == "object"
        assert isch["additionalProperties"] is False
        assert isinstance(isch["required"], list)
        # No forbidden keywords anywhere in the schema.
        blob = json.dumps(schema)
        assert not [k for k in _FORBIDDEN_KEYWORDS if f'"{k}"' in blob], name


def test_required_arrays_match_only_genuinely_required_args() -> None:
    schemas = {s["name"]: s for s in tools.tool_schemas()}
    for name, expected in _EXPECTED_REQUIRED.items():
        assert sorted(schemas[name]["input_schema"]["required"]) == sorted(expected), name


def test_update_ticket_fields_stays_free_form() -> None:
    # The strict-mode footgun avoided: optional args aren't force-required, and the free-form
    # `fields` object keeps its own additionalProperties (the model can set arbitrary keys).
    ut = next(s for s in tools.tool_schemas() if s["name"] == "update_ticket")
    fields = ut["input_schema"]["properties"]["fields"]
    assert any(branch.get("type") == "object" for branch in fields["anyOf"])
    assert "status" not in ut["input_schema"]["required"]


def test_input_models_reject_bad_payloads() -> None:
    conn = db.reset_to_seed()
    # Wrong type for k.
    with pytest.raises(tools.ToolError):
        tools.execute("search_kb", {"query": "x", "k": "lots"}, conn)
    # Missing required ticket_id.
    with pytest.raises(tools.ToolError):
        tools.execute("update_ticket", {"status": "closed"}, conn)
    # Extra field (extra="forbid" on the input models).
    with pytest.raises(tools.ToolError):
        tools.execute("route_ticket", {"ticket_id": "T-1042", "queue": "tech", "x": 1}, conn)


def test_tool_error_payload_shape() -> None:
    err = tools.ToolError("update_ticket", "boom")
    payload = err.to_result()
    assert payload == {"error": "boom", "tool": "update_ticket", "is_error": True}


# --- T2: tool -> backend execution -------------------------------------------


def test_lookup_customer_returns_seeded_customer() -> None:
    conn = db.reset_to_seed()
    out = tools.execute("lookup_customer", {"email": "jane@acme.com"}, conn)
    assert out["plan"] == "Pro"
    assert out["status"] == "active"
    assert out["flags"]["double_charge_detected"] is True
    assert out["customer"]["id"] == "C-001"
    # Recent tickets for the customer are included (T-1042 is Jane's).
    assert any(t["id"] == "T-1042" for t in out["recent_tickets"])


def test_lookup_customer_by_id_and_missing() -> None:
    conn = db.reset_to_seed()
    assert tools.execute("lookup_customer", {"customer_id": "C-002"}, conn)["plan"] == "Enterprise"
    with pytest.raises(tools.ToolError):
        tools.execute("lookup_customer", {"email": "nobody@nowhere.com"}, conn)
    with pytest.raises(tools.ToolError):
        tools.execute("lookup_customer", {}, conn)  # no selector


def test_search_kb_returns_refund_chunk_in_top_k() -> None:
    conn = db.reset_to_seed()
    out = tools.execute("search_kb", {"query": "duplicate charge refund", "k": 4}, conn)
    ids = [hit["chunk_id"] for hit in out["results"]]
    assert "kb-refund-001" in ids
    assert len(out["results"]) == 4
    assert all("score" in hit for hit in out["results"])


def test_state_change_tools_mutate_only_when_executed() -> None:
    conn = db.reset_to_seed()
    before = db.get_ticket(conn, "T-1042")["status"]
    assert before == "open"
    # Building args / schemas does not touch the DB — only execute() mutates.
    _ = tools.REGISTRY["update_ticket"].schema()
    assert db.get_ticket(conn, "T-1042")["status"] == "open"

    upd = tools.execute("update_ticket", {"ticket_id": "T-1042", "status": "pending_refund"}, conn)
    assert upd["ticket"]["status"] == "pending_refund"
    assert db.get_ticket(conn, "T-1042")["status"] == "pending_refund"

    routed = tools.execute("route_ticket", {"ticket_id": "T-1050", "queue": "tech"}, conn)
    assert routed["ticket"]["queue"] == "tech"

    esc = tools.execute(
        "escalate", {"ticket_id": "T-1055", "level": "urgent", "rationale": "x"}, conn
    )
    assert esc["ticket"]["status"] == "escalated"


def test_send_reply_returns_message_id_and_writes_nothing() -> None:
    conn = db.reset_to_seed()
    out = tools.execute("send_reply", {"to": "jane@acme.com", "body": "hi", "citations": []}, conn)
    assert out["message_id"].startswith("msg-")
    # Deterministic for the same args.
    again = tools.execute("send_reply", {"to": "jane@acme.com", "body": "hi"}, conn)
    assert again["message_id"] == out["message_id"]


def test_draft_reply_returns_slot_and_writes_nothing() -> None:
    conn = db.reset_to_seed()
    before = db.get_ticket(conn, "T-1042")
    out = tools.execute(
        "draft_reply",
        {"body": "Your dup charge will be refunded.", "citations": ["kb-refund-001"]},
        conn,
    )
    assert out["ok"] is True
    assert out["faithfulness"] is None  # filled in Split 04, slot only here
    # No backend write.
    assert db.get_ticket(conn, "T-1042") == before


def test_update_ticket_missing_ticket_is_a_tool_error() -> None:
    conn = db.reset_to_seed()
    with pytest.raises(tools.ToolError):
        tools.execute("update_ticket", {"ticket_id": "T-9999", "status": "closed"}, conn)


def test_unknown_tool_raises_tool_error() -> None:
    conn = db.reset_to_seed()
    with pytest.raises(tools.ToolError):
        tools.execute("delete_everything", {}, conn)


# --- T3 + E4: class map is a code constant the model can't set ----------------


def test_tool_class_matches_appendix_a() -> None:
    assert tools.TOOL_CLASS == {
        "lookup_customer": ToolClass.read,
        "search_kb": ToolClass.read,
        "draft_reply": ToolClass.read_class,
        "send_reply": ToolClass.state_change,
        "update_ticket": ToolClass.state_change,
        "route_ticket": ToolClass.state_change,
        "escalate": ToolClass.state_change,
    }


def test_model_output_cannot_alter_a_tools_class() -> None:
    # E4: a "cls" key in the model's args is rejected (input models forbid extras) and the class
    # comes from TOOL_CLASS, never from the call. No path exists for the model to downgrade it.
    conn = db.reset_to_seed()
    with pytest.raises(tools.ToolError):
        tools.execute(
            "update_ticket",
            {"ticket_id": "T-1042", "status": "closed", "cls": "read"},
            conn,
        )
    assert tools.TOOL_CLASS["update_ticket"] is ToolClass.state_change
    # Tool.cls is the schema's source of truth; the schema dict never exposes a class field.
    assert "cls" not in tools.REGISTRY["update_ticket"].schema()
    assert "class" not in json.dumps(tools.tool_schemas())


# --- T4: BM25 determinism ----------------------------------------------------


def test_bm25_is_deterministic_across_runs_and_dbs() -> None:
    query = "password reset link expired"
    runs = []
    for _ in range(3):
        conn = db.reset_to_seed()
        out = tools.execute("search_kb", {"query": query, "k": 5}, conn)
        runs.append([(hit["chunk_id"], hit["score"]) for hit in out["results"]])
    assert runs[0] == runs[1] == runs[2]


def test_search_kb_respects_k() -> None:
    conn = db.reset_to_seed()
    assert len(tools.execute("search_kb", {"query": "billing", "k": 2}, conn)["results"]) == 2
    assert len(tools.execute("search_kb", {"query": "billing", "k": 0}, conn)["results"]) == 0


def test_search_kb_empty_corpus_returns_nothing() -> None:
    conn = db.connect()
    db.init_schema(conn)  # tables exist, but no seed -> empty kb_chunks
    assert tools.execute("search_kb", {"query": "anything"}, conn)["results"] == []


# --- E5: grounding target ----------------------------------------------------


def test_grounding_target_is_retrievable() -> None:
    conn = db.reset_to_seed()
    out = tools.execute("search_kb", {"query": "duplicate charge refund policy"}, conn)
    top_ids = [hit["chunk_id"] for hit in out["results"]]
    assert "kb-refund-001" in top_ids


# --- E6: clean gate-readiness seam -------------------------------------------


def test_gate_readiness_handshake() -> None:
    # Everything Split 03's gate needs is exported: the registry, the class map, per-tool execute.
    assert set(tools.TOOL_CLASS) == set(tools.REGISTRY)
    for name, tool in tools.REGISTRY.items():
        assert callable(tool.execute)
        assert tools.TOOL_CLASS[name] is tool.cls
    assert callable(tools.execute) and callable(tools.tool_schemas)


def test_tools_layer_does_not_gate_state_changes() -> None:
    # The clean seam (E6): execute() *performs* regardless of class — it never withholds a
    # state-change pending approval. The gate (Split 03) is the only thing that decides.
    conn = db.reset_to_seed()
    assert tools.TOOL_CLASS["update_ticket"] is ToolClass.state_change
    out = tools.execute("update_ticket", {"ticket_id": "T-1042", "status": "closed"}, conn)
    assert out["ticket"]["status"] == "closed"  # fired immediately, no approval step
    assert db.get_ticket(conn, "T-1042")["status"] == "closed"
