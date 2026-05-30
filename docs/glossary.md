# Glossary

- **Thesis** — A specific reason to be long or short a name, with a defined exit condition. The unit of work the Analyst produces.
- **Thesis type** — One of:
  - **Value** — priced below intrinsic estimate; mean-reversion to fundamentals.
  - **Catalyst** — specific upcoming event (earnings, regulatory decision, spin-off, etc.) expected to re-rate the name.
  - **Momentum** — trend-following; price/volume action implies continuation.
  - **Mean-reversion** — short-term oversold/overbought reversal, distinct from the longer-horizon value case.
- **Conviction** — 1–5 scale on how strongly the Analyst believes the thesis. 1 = speculative, 5 = high-conviction. Drives suggested size.
- **Position** — A held name, linked back to the thesis that opened it. Open until closed (sold / stopped out / superseded).
- **Fill** — A reported execution against a suggested thesis. May be full, partial, or skipped.
- **Exit condition** — The pre-committed criterion for closing the position (price target, time-based, catalyst-resolved, stop-loss).
- **Post-mortem** — Reviewer's write-up on a closed position: outcome (PnL, hit-rate vs. thesis) and lessons (what to keep doing, what to adjust).
- **PM run** — One execution of the Portfolio Manager, identified by `pm_run_id`. Groups that run's accept/reject decisions in `pm_decisions` and backs the web UI's "Today" page.
- **Web UI** — The optional local FastAPI + Jinja2 front end (`traders.web`, `web` extra). Read-only over agent tables; the only writes go through the feedback path.
- **CSRF token** — Per-session token guarding the web UI's feedback form submissions (slice 13); required on every POST so a third-party page can't forge a write.
