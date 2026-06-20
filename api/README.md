# api — Relay HTTP layer

A **thin FastAPI adapter** that exposes the `relay` engine over HTTP. This layer is an *adapter,
not a brain*: the gate, agent loop, faithfulness check, and cost accounting all stay in `core`.
If you deleted it, the engine would still work — you'd just lose the network interface.

**Depends on:** `core` (the installed `relay` package).

## The two jobs

1. **Serialize the engine over HTTP.** `POST /handle` runs a ticket and returns a **RunView** —
   a superset of the engine's `Outcome` (§11) with the presentation enrichments the UI binds to:
   the ordered trace (reads + drafted reply + citations + faithfulness + the gated write), the
   touched backend records (customer + ticket + a proposed diff), and the cost breakdown
   (`by_call` + tokens + latency). RunView never *renames* a §11 field — one serialization.
2. **Solve suspend/resume across two requests.** `/handle` and `/approve` are separate HTTP calls,
   so each run gets a **durable per-run file DB** (`runs.py`, the run store). `/handle` creates +
   runs it; `/approve` reopens it by `run_id` and resumes the exact suspended loop. **The
   never-acts-without-approval invariant holds across the HTTP boundary** — a state-changing write
   does not fire on `/handle`, only on `/approve`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/handle` | Run a ticket. Body `{ ticket, provider, model?, policy }` → **RunView**. A gated write returns `status="awaiting_approval"` with `actions_pending` + a proposed records diff. |
| `POST` | `/approve` | Resume a suspended run. Body `{ run_id, decisions: [{approval_id, decision, edited_args?}] }`. Decides **all** pending actions of the turn (turn-granular). |
| `GET` | `/examples` | The four worked tickets from `core/examples/` (injection flagged `lock`). |
| `GET` | `/config` | Selectable providers / models / policies — model IDs sourced from `core` (no drift). |
| `GET` | `/health` | `{ status, providers_available: { anthropic, openai } }` — reflects key presence. |
| `GET` | `/` | Redirects to the mounted prototype (`/app/Relay.dc.html`) for a single-origin demo. |

Every error is a **structured envelope** — `{ error: { type, message, provider?, env_var?,
retriable? } }` — never a 500 stack trace: missing key → `424 missing_key`, bad body →
`400 bad_request`, lost run → `404 run_not_found`, upstream failure → `502 provider_error`.

## Run it

```bash
# from the repo root, with the engine installed (pip install -e core/[providers]) ...
pip install -e api/                      # install the API + FastAPI/uvicorn
cp api/.env.example .env                 # add ANTHROPIC_API_KEY and/or OpenAI/Azure creds
uvicorn relay_api.app:app --reload       # http://127.0.0.1:8000  (docs at /docs)
```

Open <http://127.0.0.1:8000/> for the static frontend (Split 08 makes it call the API), or hit the
JSON API directly:

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/handle \
  -H 'content-type: application/json' \
  -d '{"ticket":"I was charged twice (order A-4471), please refund. — jane@acme.com",
       "provider":"anthropic","policy":"strict"}'
# → status "awaiting_approval" + a run_id; then:
curl -s -X POST localhost:8000/approve \
  -H 'content-type: application/json' \
  -d '{"run_id":"<run_id>","decisions":[{"approval_id":"<id>","decision":"allow"}]}'
```

### Docker (single-process demo)

```bash
docker build -f api/Dockerfile -t relay-api .   # build from the repo root
docker run --rm -p 8000:8000 --env-file .env relay-api
```

## Configuration (env)

| Var | Effect |
|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Provider keys (read by the engine). `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY` enable the Azure gpt-5.5 deployment. |
| `RELAY_API_STORE_DIR` | Base dir for per-run file DBs (default: a temp dir). |
| `RELAY_APP_DIR` | Static frontend dir mounted at `/app` (default: repo-root `app/`). |
| `RELAY_CORS_ORIGINS` | Comma-separated allowed origins (default: localhost dev). `*` allows all. |

## Tests

```bash
cd api
python -m pytest -m "not api"     # Tier-1: no key, deterministic (StubProvider via dep-override)
python -m pytest -m api           # Tier-2: live provider round-trip (needs a key; auto-skipped)
```
