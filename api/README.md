# api: Relay HTTP layer

A **thin FastAPI adapter** that exposes the `relay` engine over HTTP. This layer is an adapter, not a brain: the gate, agent loop, faithfulness check, and cost accounting all stay in `core`. If you deleted it, the engine would still work; you'd just lose the network interface.

**Depends on:** `core` (the installed `relay` package).

## The two jobs

1. **Serialize the engine over HTTP.** `POST /handle` runs a ticket and returns a **RunView**, a superset of the engine's `Outcome` with the presentation enrichments the UI binds to: the ordered trace (reads, the drafted reply, citations, faithfulness, and the gated write), the touched backend records (customer, ticket, and a proposed diff), and the cost breakdown (`by_call`, tokens, and latency). RunView never *renames* a field from the engine; it is one serialization.
2. **Solve suspend/resume across two requests.** `/handle` and `/approve` are separate HTTP calls, so each run gets a **durable per-run file DB** (`runs.py`, the run store). `/handle` creates and runs it; `/approve` reopens it by `run_id` and resumes the exact suspended loop. The never-acts-without-approval invariant holds across the HTTP boundary: a state-changing write does not fire on `/handle`, only on `/approve`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/handle` | Run a ticket. Body `{ ticket, provider, model?, policy }` returns a **RunView**. A gated write returns `status="awaiting_approval"` with `actions_pending` and a proposed records diff. |
| `POST` | `/approve` | Resume a suspended run. Body `{ run_id, decisions: [{approval_id, decision, edited_args?}] }`. Decides **all** pending actions of the turn (turn-granular). |
| `GET` | `/examples` | The four worked tickets from `core/examples/` (injection flagged `lock`). |
| `GET` | `/config` | Selectable providers, models, and policies. Model IDs are sourced from `core`, so there is no drift. |
| `GET` | `/health` | `{ status, providers_available: { anthropic, openai } }`, reflecting key presence. |
| `GET` | `/` | Redirects to the mounted prototype (`/app/Relay.dc.html`) for a single-origin demo. |

Every error is a **structured envelope**, `{ error: { type, message, provider?, env_var?, retriable? } }`, never a 500 stack trace: missing key returns `424 missing_key`, a bad body returns `400 bad_request`, a lost run returns `404 run_not_found`, and an upstream failure returns `502 provider_error`.

## Run it

**One command.** From the repo root, `python demo.py` brings the whole stack up on one origin with a fresh seed and prints the URL (`make demo` on Unix):

```bash
pip install -e "core/[providers]" && pip install -e api/   # once
python demo.py                 # live on whatever key is in .env (http://127.0.0.1:8000/)
python demo.py --stub          # offline demo: canned data, no key, no network
```

`demo.py` wipes the per-run store on each launch (a clean cold start with no leftover state), points the mount at this repo's `app/` and `core/examples/`, and degrades gracefully with no key (the missing-key banner, or `--stub` for the deterministic offline path, labelled `stub: true` on `/health`).

Or run `uvicorn` directly:

```bash
cp api/.env.example .env                 # add ANTHROPIC_API_KEY and/or OpenAI/Azure creds
uvicorn relay_api.app:app --reload       # http://127.0.0.1:8000  (docs at /docs)
```

Open <http://127.0.0.1:8000/> for the static frontend, or hit the JSON API directly:

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/handle \
  -H 'content-type: application/json' \
  -d '{"ticket":"I was charged twice (order A-4471), please refund. jane@acme.com",
       "provider":"anthropic","policy":"strict"}'
# returns status "awaiting_approval" and a run_id; then:
curl -s -X POST localhost:8000/approve \
  -H 'content-type: application/json' \
  -d '{"run_id":"<run_id>","decisions":[{"approval_id":"<id>","decision":"allow"}]}'
```

### Docker (single-process demo)

From the **repo root** (the build context needs `core/`, `api/`, and `app/`):

```bash
# Compose, the one-command path (reads keys from .env if present):
docker compose up --build                    # live demo  -> http://localhost:8000/
RELAY_STUB=1 docker compose up --build        # offline canned demo (no key, no model calls)

# Or plain docker:
docker build -f api/Dockerfile -t relay .
docker run --rm -p 8000:8000 --env-file .env relay        # live
docker run --rm -p 8000:8000 -e RELAY_STUB=1 relay        # offline stub
```

Keys are passed at **runtime** (never baked into the image). The container serves the API and static frontend; the prototype pulls React, ReactDOM, and Babel from the unpkg CDN, so the **browser** needs internet. `/health` is the container healthcheck (it works with or without a key). With no key and no `RELAY_STUB`, the app still loads and shows the honest missing-key banner.

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
</content>
