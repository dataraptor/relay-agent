# Hero media

The README's hero frames (the UI spec shot list), light theme, monochrome plus indigo.

| Frame | What it shows |
|---|---|
| `04-mobile-GATE.png` | **The hero:** `update_ticket` paused at the approval gate; the write has not fired. |
| `05-mobile-committed.png` | Post-Approve: the write committed, records flipped to `pending_refund`, and the cost line. |
| `06-mobile-injection.png` | The gate holding on the prompt-injection ticket: "the gate is code, and it held." |

## How these were produced (and why it's honest)

This repo runs headless (there is no interactive browser session here), so the frames are **rendered deterministically** by [`build_frames.py`](build_frames.py) rather than captured by clicking through a live animation. Each frame:

- reuses the DC prototype's **verbatim design tokens and gate-sheet markup** (`app/Relay.dc.html`),
- is populated with a **real `RunView` fixture** dumped from the backend projection (`app/tests/fixtures/billing_awaiting.json` and the like, themselves generated from a real engine run),
- and is rasterised by headless Chrome/Edge at 390px @2x.

So what you see is what the live UI renders at that beat, with the same CSS and the same backend data shape, generated reproducibly instead of timed against the staged reveal. The intermediate HTML lives in `_frames/`. Regenerate everything with:

```bash
python docs/media/build_frames.py
```

The frames depict the **offline/stub money-demo path**, which deterministically lands all seven beats (triage, reads, cited reply, pause, approve, commit, and `$/ticket`). On live Azure gpt-5.5 the model often routes straight to a queue instead of drafting a reply, which is a documented behavior; see the [leaderboard notes](../eval/leaderboard-20260620.txt).
</content>
