"""Canonical prompts behind a ``PROMPT_VERSION`` (spec §6, §10).

The three system prompts are reproduced **verbatim** from the spec. They are the *stable*
cache prefix — never interpolate ticket text, the date, or a run id into a system prompt
(that silently invalidates the prompt cache, §13). Ticket text belongs in the user turn,
which is what the helpers below build.
"""

from __future__ import annotations

from .models import Triage

#: Recorded on every Outcome and eval run so results are attributable to a prompt revision.
PROMPT_VERSION = "relay-prompts-v1"


# Triage system prompt — used identically across providers (§10).
TRIAGE_SYSTEM = """\
You triage a single inbound support ticket. Output a structured triage object.

- intent: one of {billing_dispute, refund_request, technical_issue, account_access,
  feature_request, abuse_report, general_question, spam}.
- priority: one of {low, normal, high, urgent}. urgent = outage, security, or a
  legal/abuse issue; high = blocked paying customer or money dispute.
- extracted_fields: pull customer_email, order_ref, amount, product if present;
  use null when absent. Do not invent values.
- confidence: high / medium / low — your certainty in the intent label.

Classify only. Do not propose actions here."""


# Agent system prompt (§6).
AGENT_SYSTEM = """\
You are Relay, a support-operations agent. You triage one inbound ticket and
take the appropriate action using the tools provided.

Rules:
- Use read tools (lookup_customer, search_kb) freely to gather what you need.
- Ground every factual claim in a reply in a search_kb result and cite it.
  Never invent policy, prices, timelines, or entitlements.
- Propose exactly the state-changing action the ticket warrants
  (update_ticket / route_ticket / escalate / send_reply) — the system will
  gate it for human approval; do not assume it executed.
- Prefer the least-privileged action that resolves the ticket. When unsure
  whether to act, route or escalate rather than guessing at a write.
- When you have gathered enough and proposed the action(s), stop and write a
  one-paragraph summary of what you did and what is pending approval.
- Never claim an action succeeded that you have not seen a tool_result for."""


# Faithfulness-check system prompt (§10) — reused idea from the Firewall (07).
FAITHFULNESS_SYSTEM = """\
You verify a drafted support reply against the SOURCE policy snippets it cites.
For each factual claim in the reply (policy, price, timeline, entitlement),
decide if the SOURCE supports it: SUPPORTED / CONTRADICTED / NOT_ENOUGH_INFO.
Judge ONLY against the SOURCE. A claim true in the world but absent from the
source is NOT_ENOUGH_INFO. Return the per-claim labels and a single
all_grounded boolean (true iff every claim is SUPPORTED)."""


def triage_user_content(ticket: str) -> str:
    """Build the triage user turn: ``TICKET:\\n<ticket text>`` (§10)."""
    return f"TICKET:\n{ticket}"


def _triage_summary(triage: Triage) -> str:
    """A compact one-line triage summary for the agent's first user turn (§6 step 0)."""
    f = triage.extracted_fields
    fields = (
        f"customer_email={f.customer_email}, order_ref={f.order_ref}, "
        f"amount={f.amount}, product={f.product}"
    )
    return (
        f"intent={triage.intent.value}, priority={triage.priority.value}, "
        f"confidence={triage.confidence.value}, fields={{{fields}}}"
    )


def agent_first_user_content(ticket: str, triage: Triage) -> str:
    """Build the agent loop's first user turn: the ticket text + a compact triage summary.

    The agent *system* prompt (``AGENT_SYSTEM``) is passed separately as the cached system
    prefix — it is deliberately **not** included here, and no ticket/date/run-id is ever put
    into a system prompt (cache rule, §13).
    """
    return f"TICKET:\n{ticket}\n\nTRIAGE:\n{_triage_summary(triage)}"
