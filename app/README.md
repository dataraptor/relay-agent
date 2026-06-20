# app — the frontend

The user-facing client: a **high-fidelity React prototype wired to the live Relay API.** It renders
what the backend returns and handles approval interaction; it holds no business logic of its own. The
real artifact is `Relay.dc.html` — a `<x-dc>` template + an inline `Component extends DCLogic` class —
driven by `support.js` (the "dc-runtime": it loads React/ReactDOM/Babel from unpkg, transpiles the
inline component, and mounts it). The data source is the real API (`api.js` + `map.js`), not a
simulation.

> The framework is **not Flutter** — there is no `lib/`. An earlier scaffolding template described one;
> this repo's frontend is the DC prototype documented here. For the project story, the architecture, and
> the leaderboard, start at the [root README](../README.md).

**Depends on:** `api` (over the network, at runtime).

---

## How it's wired (Split 08)

Split 08 wired this prototype to the live backend — the simulated `buildScenario()` is gone:

- **`api.js`** — a thin client over the Split 07 routes (`/config`, `/examples`, `/handle`,
  `/approve`, `/health`) with structured-error → banner shaping. (`window.RelayApi`)
- **`map.js`** — a pure `runViewToState(runView)` mapper: backend **RunView → the view-model**
  `renderVals()` already consumes. The data source changed; the UI did not. (`window.RelayMap`)
- **`Relay.dc.html`** — the same template + `renderVals()`; only the data methods now call the API
  and reveal the **real** trace.

### Run it (served by the API)

The app is served by the API's static mount (Split 07). From the repo root, with the engine + API
installed and a provider key in `.env`:

```bash
uvicorn relay_api.app:app --reload      # then open http://127.0.0.1:8000/  (→ /app/Relay.dc.html)
```

`support.js` fetches React/ReactDOM/Babel (pinned 18.3.1 + SRI) from unpkg on first load
(**serving option A**, confirmed for Split 08), so the page needs network access the first time.

### Happy-path money demo (manual smoke)

1. Open `/`. The composer loads the **billing** example (text + provider/model/policy) from
   `/config` + `/examples` — not hardcoded.
2. Tap **Run**. Triage fills, the two read rows appear, the cited reply streams in, and
   `update_ticket`/`route_ticket` **pauses** — the gate sheet rises with the **real** args +
   rationale + diff. *(The DB shows no write yet.)*
3. Tap **Approve**. `POST /approve` fires; the write commits, the records panel flips to committed,
   and the cost line shows the real `$/ticket`. **The write never fires before Approve.**

### Tests

```bash
cd app && npm test           # Tier-1: mapper + client + component render (node:test, no network/key)
npm run coverage             # same, with coverage (map.js + api.js ≥ 85%)
npm run e2e                  # cross-stack e2e (Split 10): boots a real server, real HTTP, asserts
                             #   the never-acts-without-approval invariant through core→api→app.
                             #   Default = deterministic stub path (no key). RELAY_E2E_LIVE=1 also
                             #   runs it against a live provider (Tier-2).
python tests/e2e_live.py        # Tier-2: real money-demo end-to-end over HTTP (needs a key in .env)
python tests/e2e_injection.py   # Tier-2: the injection dark-beat (gate holds regardless of prompt)
python tests/_gen_fixtures.py   # regenerate the canned RunView fixtures after a contract change
```

### The cross-stack e2e (`tests/crossstack.e2e.js`) — Split 10's crown jewel

It spawns a real `uvicorn relay_api.app:app`, drives `/config` → `/examples` → `/handle` →
`/approve` over real `fetch`, opens the run's SQLite DB on disk (`node:sqlite`) to assert **0
state-change writes before Approve** (`assert_no_unapproved_writes`), and feeds every real `RunView`
through the same `map.js` the browser runs — proving the UI never shows a committed write before
Approve, and shows it committed only after. Billing + injection, deterministic in CI and live on a
real provider when keyed. It needs Python with the stack installed (`make install`).

## What's new (Split 09): every edge state, multi-pending, a11y

Split 09 makes the live app honest and unbreakable under every §20/§9 edge — all driven by **real
RunView/error fields**, never a simulated flag:

- **Edge states** (mapper-driven): low-confidence + spam triage hints, ambiguous "0 writes" notice,
  `all_grounded=false` (alert + per-claim list + "model may revise"), `deny`→blocked, `auto` write,
  below-cache-floor caption, missing-key banner, `status=error` (message + partial cost). Markers the
  RunView does not yet carry (`refusal` / `step_capped` / step `is_error`) are consumed *if present*
  and degrade to absent otherwise — see the Split-09 carry-forwards in `tmp/split/PROGRESS.md`.
- **Multi-pending turn-granular gate (R2):** when a turn proposes >1 state-change, the gate stacks
  them; **Resume stays disabled until every one is decided**, then sends a single `/approve`
  `decisions` array (a partial batch is refused client-side — every `tool_use` needs a `tool_result`).
- **`send_reply` irreversible variant (R3):** the one red line + the exact reply body + citations.
- **Provider replay (R4):** switch provider → Run again → the cost line tweens between the two
  providers' **real** numbers (no fabricated factor); the cache caption is honest per provider.
- **Injection dark-beat:** running the locked `injection` example shows "the gate is code — it held."

### Accessibility & responsive — manual verification checklist (T6/T7, E5)

The a11y *hooks* are CI-tested (`tests/a11y.test.js`) and the component render layer is exercised
headless for every state (`tests/component.test.js`, E1/E7). A browser/axe pass is manual — run the
served app (`uvicorn relay_api.app:app`) and confirm:

- **Keyboard:** Tab reaches every control; on suspend, focus moves into the gate and is trapped;
  `Enter`=Approve, `E`=edit, Reject requires focus+activate; batch mode tabs each action then Resume;
  closing the gate returns focus to the trace. 2px indigo focus ring on every control.
- **Screen reader:** opening the gate announces "Approval required: …" (assertive); the reply streams
  via a polite region.
- **Reduced motion** (`prefers-reduced-motion`): rises/streaming/count-up collapse to ≤80ms fades, the
  reply appears complete, **and the gate still appears + still announces** — no state is motion-only.
- **Responsive:** at 390 / 768 / 1280px the fluid single column holds and the pause behaves
  identically (bottom sheet on phone, centered modal above); targets ≥44px. (Desktop two-pane is a
  deliberate non-change — the prototype is mobile-first by design; see the Split-09 carry-forward.)
