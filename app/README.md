# app: the frontend

The user-facing client: a **high-fidelity React prototype wired to the live Relay API.** It renders what the backend returns and handles approval interaction; it holds no business logic of its own. The real artifact is `Relay.dc.html`, an `<x-dc>` template plus an inline `Component extends DCLogic` class, driven by `support.js` (the "dc-runtime": it loads React, ReactDOM, and Babel from unpkg, transpiles the inline component, and mounts it). The data source is the real API (`api.js` and `map.js`), not a simulation.

For the project story, the architecture, and the leaderboard, start at the [root README](../README.md).

**Depends on:** `api` (over the network, at runtime).

---

## How it's wired

The prototype talks to the live backend; there is no simulated scenario builder:

- **`api.js`**: a thin client over the API routes (`/config`, `/examples`, `/handle`, `/approve`, `/health`) with structured-error-to-banner shaping. (`window.RelayApi`)
- **`map.js`**: a pure `runViewToState(runView)` mapper. It turns the backend **RunView** into the view-model that `renderVals()` already consumes. The data source changed; the UI did not. (`window.RelayMap`)
- **`Relay.dc.html`**: the same template and `renderVals()`; only the data methods now call the API and reveal the **real** trace.

### Run it (served by the API)

The app is served by the API's static mount. From the repo root, with the engine and API installed and a provider key in `.env`:

```bash
uvicorn relay_api.app:app --reload      # then open http://127.0.0.1:8000/  (-> /app/Relay.dc.html)
```

`support.js` fetches React, ReactDOM, and Babel (pinned 18.3.1 with SRI) from unpkg on first load, so the page needs network access the first time.

### Happy-path money demo (manual smoke test)

1. Open `/`. The composer loads the **billing** example (text plus provider, model, and policy) from `/config` and `/examples`, not hardcoded.
2. Click **Run**. Triage fills in, the two read rows appear, the cited reply streams in, and `update_ticket` or `route_ticket` **pauses**: the gate sheet rises with the **real** args, rationale, and diff. (The DB shows no write yet.)
3. Click **Approve**. `POST /approve` fires, the write commits, the records panel flips to committed, and the cost line shows the real `$/ticket`. The write never fires before Approve.

### Tests

```bash
cd app && npm test           # Tier-1: mapper + client + component render (node:test, no network/key)
npm run coverage             # same, with coverage (map.js + api.js >= 85%)
npm run e2e                  # cross-stack e2e: boots a real server, real HTTP, asserts the
                             #   never-acts-without-approval invariant through core -> api -> app.
                             #   Default = deterministic stub path (no key). RELAY_E2E_LIVE=1 also
                             #   runs it against a live provider (Tier-2).
python tests/e2e_live.py        # Tier-2: real money-demo end-to-end over HTTP (needs a key in .env)
python tests/e2e_injection.py   # Tier-2: the injection case (gate holds regardless of prompt)
python tests/_gen_fixtures.py   # regenerate the canned RunView fixtures after a contract change
```

### The cross-stack e2e (`tests/crossstack.e2e.js`)

This is the headline test. It spawns a real `uvicorn relay_api.app:app`, drives `/config` -> `/examples` -> `/handle` -> `/approve` over real `fetch`, opens the run's SQLite DB on disk (`node:sqlite`) to assert **0 state-change writes before Approve** (`assert_no_unapproved_writes`), and feeds every real `RunView` through the same `map.js` the browser runs. This proves the UI never shows a committed write before Approve, and shows it committed only after. It covers billing and injection, runs deterministically in CI, and runs live on a real provider when keyed. It needs Python with the stack installed (`make install`).

## Edge states, multi-pending, and accessibility

The live app stays honest and unbreakable under every edge case, all driven by **real RunView and error fields**, never a simulated flag:

- **Edge states** (mapper-driven): low-confidence and spam triage hints, an ambiguous "0 writes" notice, `all_grounded=false` (an alert, a per-claim list, and a "model may revise" note), `deny`-blocked, an `auto` write, a below-cache-floor caption, the missing-key banner, and `status=error` (a message plus partial cost). Markers the RunView does not yet carry (`refusal`, `step_capped`, step `is_error`) are consumed *if present* and degrade to absent otherwise.
- **Multi-pending turn-granular gate:** when a turn proposes more than one state-change, the gate stacks them. **Resume stays disabled until every one is decided,** then sends a single `/approve` `decisions` array (a partial batch is refused client-side, because every `tool_use` needs a `tool_result`).
- **`send_reply` irreversible variant:** the one red line, the exact reply body, and citations.
- **Provider replay:** switch provider, click Run again, and the cost line tweens between the two providers' **real** numbers (no fabricated factor); the cache caption is honest per provider.
- **Injection case:** running the locked `injection` example shows "the gate is code, and it held."

### Accessibility and responsive: manual verification checklist

The accessibility *hooks* are CI-tested (`tests/a11y.test.js`) and the component render layer is exercised headless for every state (`tests/component.test.js`). A browser and axe pass is manual: run the served app (`uvicorn relay_api.app:app`) and confirm:

- **Keyboard:** Tab reaches every control; on suspend, focus moves into the gate and is trapped; `Enter` approves, `E` edits, and Reject requires focus plus activate; batch mode tabs each action then Resume; closing the gate returns focus to the trace. A 2px indigo focus ring shows on every control.
- **Screen reader:** opening the gate announces "Approval required: ..." (assertive); the reply streams via a polite region.
- **Reduced motion** (`prefers-reduced-motion`): rises, streaming, and count-up collapse to fades of 80ms or less, the reply appears complete, **and the gate still appears and still announces.** No state is motion-only.
- **Responsive:** at 390, 768, and 1280px the fluid single column holds and the pause behaves identically (a bottom sheet on phone, a centered modal above); targets are 44px or larger. (The desktop two-pane is a deliberate non-change; the prototype is mobile-first by design.)
</content>
