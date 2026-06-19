-- Relay mock backend schema (spec §11).
--
-- Seven tables. The four "agent/loop" tables (runs, llm_calls, actions_log, tool_calls)
-- are created here in Split 01 but only WRITTEN TO starting Split 03; their shapes must
-- exist and be tested now. No migration framework: synthetic data only, drop-and-recreate.
-- Each handle()/eval run gets its OWN isolated DB (a fresh seeded :memory: or temp file),
-- so the parallel eval pool never races on a shared file (§11/§13).

-- Business data ------------------------------------------------------------

CREATE TABLE customers (
    id          TEXT PRIMARY KEY,
    email       TEXT UNIQUE,
    name        TEXT,
    plan        TEXT,           -- Free | Pro | Enterprise
    status      TEXT,           -- active | past_due | suspended | ...
    mrr         REAL,
    flags_json  TEXT,           -- JSON object: {double_charge_detected, past_due, abuse_flag, ...}
    created_at  TEXT
);

CREATE TABLE tickets (
    id          TEXT PRIMARY KEY,
    customer_id TEXT,
    subject     TEXT,
    body        TEXT,
    intent      TEXT,
    priority    TEXT,
    status      TEXT,           -- open | pending_refund | escalated | closed | ...
    queue       TEXT,           -- unassigned | billing | tech | abuse | ...
    updated_at  TEXT
);

CREATE TABLE kb_chunks (
    id      TEXT PRIMARY KEY,
    source  TEXT,               -- human-readable doc title
    url     TEXT,               -- synthetic docs URL
    section TEXT,
    text    TEXT
);

-- Run state + observability ledgers ----------------------------------------

-- One row per handle() call. messages_json holds the in-flight tool-use transcript that
-- approve() reloads to resume a suspended loop (§8/§11). runs.id == Outcome.id.
CREATE TABLE runs (
    id            TEXT PRIMARY KEY,
    ticket_id     TEXT,
    provider      TEXT,
    model         TEXT,
    prompt_version TEXT,
    status        TEXT,         -- running | awaiting_approval | done | error
    step          INTEGER,
    messages_json TEXT,
    created_at    TEXT,
    updated_at    TEXT
);

-- The cost/observability ledger: ONE row per model inference (triage, each loop step that
-- emits a tool call, each faithfulness check). $/ticket = SUM(cost_usd) over the run (§13).
-- Tool execution against SQLite costs zero tokens, so token buckets live here, not on tool_calls.
CREATE TABLE llm_calls (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                TEXT,
    ticket_id             TEXT,
    kind                  TEXT,  -- triage | loop_step | faithfulness
    provider              TEXT,
    model                 TEXT,
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    cache_read_tokens     INTEGER,
    cache_creation_tokens INTEGER,
    latency_ms            INTEGER,
    cost_usd              REAL,
    ts                    TEXT
);

-- The safety/audit ledger (§8): the never-acts-without-approval invariant is checked here
-- (no state-change tool_calls row may exist without a matching actions_log decision row).
CREATE TABLE actions_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id         TEXT,
    run_id            TEXT,
    tool              TEXT,
    proposed_args_json TEXT,
    final_args_json   TEXT,      -- captures operator edits
    decision          TEXT,      -- auto | approved | rejected | blocked
    approver          TEXT,
    result_json       TEXT,
    ts                TEXT
);

-- The action log: which backend mutation actually ran, with what args. Deliberately WITHOUT
-- token columns — cost lives in llm_calls (§11).
CREATE TABLE tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    ticket_id   TEXT,
    step        INTEGER,
    tool        TEXT,
    args_json   TEXT,
    result_json TEXT,
    latency_ms  INTEGER,
    ts          TEXT
);
