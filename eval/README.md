# eval — the headline harness

The evaluation harness. It imports the installed `relay` engine directly (no server) and drives
the full **triage → gated loop → faithfulness** stack over a gold scenario set, producing a
**leaderboard** with two registers:

- **Deterministic safety (CI gate · 100% · no API key):** never-acts-without-approval, gate-policy
  correctness, schema validity — proven on the frozen `must_gate/` subset with the no-network
  `StubProvider` under the `strict` policy. This is the credibility of the gate: *proven*, not
  "the model usually asks". It is free and runs on every commit.
- **Distributional quality (mean ± spread over N≥3, both providers):** routing, field-extraction,
  action-correctness, reply-faithfulness, plus `$/ticket` and latency per provider. LLM output is
  not reproducible, so these are always reported distributionally — never a single number.

It **does not** re-implement any engine logic — it calls `relay.handle` / `relay.approve` and
reads the ledger (`$/ticket = SUM(llm_calls.cost_usd)`).

## Run it

```bash
# from the repo root (relay must be installed: pip install -e core/)
python -m eval.run --tier1                       # deterministic safety gate, NO key, free
python -m eval.run --quick --provider openai      # fast smoke on one provider
python -m eval.run --repeats 3 --provider both     # full distributional leaderboard
```

Flags: `--provider {anthropic,openai,both}` · `--repeats R` (default 3) · `--quick` (small
tier-complete subset) · `--tier1`/`--no-key` (deterministic only) · `--out PATH` (jsonl, default
`eval/runs/<ts>.jsonl`) · `--scenarios DIR` · `--workers N` (pool size, default 6).

Missing a provider key is handled gracefully: the run prints a note and falls back to the
deterministic tier rather than crashing. The deterministic tier returns a non-zero exit (and prints
`DETERMINISTIC SAFETY GATE FAILED`) if any un-approved write, gate-policy, schema, or must-gate
pause check regresses — so it can gate CI directly.

## Gold scenarios (`scenarios/<split>/*.yaml`)

~36 human-reviewed tickets spanning the §10 intents and the Appendix C case mix
(clean-resolvable, grounded-reply, ambiguous, adversarial/injection, forbidden-action), split into:

| Split | Count | Role |
|---|---|---|
| `must_gate` | 10 | **The frozen safety contract — NEVER tuned against.** Every state-change tool (send_reply / update_ticket / route_ticket / escalate) is represented; the deterministic tier proves each pauses under `strict`. |
| `tuning` | 18 | The distributional working set. |
| `held_out` | 8 (~22%) | The frozen distributional slice — reported separately with a small-n caption. |

Each scenario's `expect` block is the ground truth scored by `metrics.py`. The folder name sets the
split; labels are validated against the engine's own enums + tool registry, so a typo is a
load-time error, not a silently-wrong metric.

## Layout

```
eval/
  scenario.py    # the Scenario schema + loader (validates labels)
  metrics.py     # metric definitions, each tagged deterministic vs distributional
  run.py         # the harness, the pool, the leaderboard printer, the CLI (python -m eval.run)
  scenarios/     # gold tickets, by split
  runs/          # persisted *.jsonl (gitignored)
  tests/         # Tier-1 (no key) + a Tier-2 @api smoke
```

**Depends on:** `core` (imported directly, no server required).
