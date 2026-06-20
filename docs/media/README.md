# Hero media

The README's hero frames (UI spec §10 shot list), light theme, monochrome + indigo.

| Frame | What it shows |
|---|---|
| `04-mobile-GATE.png` | **The hero** — `update_ticket` paused at the approval gate; the write has not fired. |
| `05-mobile-committed.png` | Post-Approve — the write committed, records flipped to `pending_refund`, the cost line. |
| `06-mobile-injection.png` | The gate holding on the prompt-injection ticket: "the gate is code — it held." |

## How these were produced (and why it's honest)

This repo runs headless — there is no interactive browser session here — so the §10 frames are
**rendered deterministically** by [`build_frames.py`](build_frames.py) rather than captured by clicking
through a live animation. Each frame:

- reuses the DC prototype's **verbatim design tokens and gate-sheet markup** (`app/Relay.dc.html`), and
- is populated with a **real `RunView` fixture** dumped from the Split-07 backend projection
  (`app/tests/fixtures/billing_awaiting.json` etc., themselves generated from a real engine run),
- rasterised by headless Chrome/Edge at 390px @2×.

So what you see is what the live UI renders at that beat — same CSS, same backend data shape —
generated reproducibly instead of timed against the staged reveal. The intermediate HTML lives in
`_frames/`. Regenerate everything with:

```bash
python docs/media/build_frames.py
```

The frames depict the **offline/stub money-demo path**, which deterministically lands all seven beats
(triage → reads → cited reply → pause → approve → commit → `$/ticket`). On live Azure gpt-5.5 the model
often routes straight to a queue instead of drafting a reply — a documented behavior, see the
[leaderboard notes](../eval/leaderboard-20260620.txt).
