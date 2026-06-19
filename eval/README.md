# eval

The evaluation harness — measures how well the engine itself performs.

This is offline, development-time work, separate from the shipped product. It runs the
engine against labeled datasets and reports quality metrics, so the system's accuracy
can be quantified and proven rather than assumed. End users never touch this code.

**Contains:** labeled datasets, the scripts that run the evaluations, and the
generated metric reports.

**Depends on:** `core` (imported directly, no server required).
