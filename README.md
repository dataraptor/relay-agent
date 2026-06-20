# relay-agent
Agentic ops-automation agent (support/RevOps triage) with a human-approval gate on every write action — multi-provider (Claude + OpenAI), eval-proven, cost-traced.

## Quickstart (the one-command demo)

```bash
pip install -e "core/[providers]" && pip install -e api/   # install the engine + HTTP adapter
python demo.py            # live on whatever provider key is in .env; open http://127.0.0.1:8000/
python demo.py --stub     # offline demo — canned data, no key or network required
```

Then walk the **money demo**: select the *billing* ticket → Run → triage → reads → a cited,
grounded reply → `update_ticket` **pauses at the gate** → Approve fires the write → `$/ticket`
settles. The write never fires before Approve — that safety invariant is asserted end-to-end across
the whole stack (`cd app && npm run e2e`). Set `ANTHROPIC_API_KEY` and/or OpenAI/Azure creds in
`.env`; with no key the demo still loads and shows the missing-key banner.

> Full architecture, leaderboard, and writeup land in the final hardening pass (Split 11). The
> living build log is `tmp/split/PROGRESS.md`.
