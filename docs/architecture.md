# Architecture

Four sequential agents plus a weekly reviewer, all running as plain Python modules against a shared SQLite database.

## Cadence

- **Daily, post-close.** Scout → Researcher → Analyst → Portfolio Manager runs once per day after US market close. EU close is earlier; the system runs after both have closed.
- **Weekly.** Reviewer runs on closed positions over the past week and writes post-mortems.

## Agents

### Scout

Filters the watchlist (S&P 100 + EuroStoxx 50) into a small set of candidates worth deeper investigation. Heuristic in early slices; may grow LLM-assisted later. Writes to `candidates`.

### Researcher

For each candidate, gathers context — recent filings, news, fundamentals — and writes a structured note to `research_notes`. Sources tracked explicitly so theses can be audited.

### Analyst

Reads the day's research notes and produces zero-or-more theses per candidate. Each thesis has a `thesis_type` (value / catalyst / momentum / mean-reversion), direction, conviction (1–5), suggested size, exit condition, and rationale.

### Portfolio Manager

Final filter. Cross-checks open theses against current open `positions` for concentration and correlation. Emits a daily report — the human-readable summary the user actually consumes.

### Reviewer (weekly)

Walks closed positions and writes post-mortems with outcome and lessons learned. Feeds back into prompt context for Analyst/Researcher.

## Data flow

```
watchlist.json
   ↓
[Scout]            →  candidates
   ↓
[Researcher]       →  research_notes
   ↓
[Analyst]          →  theses
   ↓
[Portfolio Mgr]    →  daily report
   ↓
(manual execution by the user)
   ↓
[Feedback CLI  or  Web UI]  →  positions, feedback
   ↓
[Reviewer, weekly] →  post_mortems
```

The **web UI** (`traders.web`, optional `web` extra) is an alternative front end over the same database. It reads agent output (candidates, theses, the PM report, positions, post-mortems) through a dedicated read layer and reports execution through the *same* `traders.feedback` functions the CLI uses — there is no second write path into `positions`. Agents remain CLI/orchestrator-driven; the UI never runs them.

## Backtest harness (offline)

The **backtest harness** (`traders.backtest`, slice 17) is an offline evaluation
surface, not a fifth agent. It replays a `LearnedParameters` set over a
`PriceHistory` (`traders.prices`) by composing the *pure* decision functions the
live agents expose — `scout.filter_candidates`, the `ThesisGenerator`,
`portfolio.evaluate`, `post_mortems.compute_pnl_pct`, `metrics.score` — so a
parameter change is scored against months of history in seconds instead of the
trickle of live closed positions. It runs entirely in memory and **never writes
to the live tables**; the only persistence it touches is the read-only `prices`
store (`migrations/006_prices.sql`), and even that is optional — a deterministic
`synthetic_history` needs no rows. `compare_params` / `backtest_experiment` make
slice-16 Optimizer proposals testable (baseline vs the one proposed change) over
the same history.

Slice 24 turns that into an **apply gate**: `gate_experiment` splits the window
in-sample / out-of-sample, replays baseline and candidate over each half, and
recommends applying a proposal only when the candidate beats the baseline
*out-of-sample* **and** its per-trade Sharpe survives deflation for the number of
proposals tried (`traders.deflated_sharpe`, a stdlib Probabilistic/Deflated Sharpe
Ratio — more proposals raise the bar, so the optimizer can't fish). `traders
optimize --apply` consults the gate and refuses an un-improving change unless
`--force` is given; the human `--apply` gate and paper-only posture are unchanged.

## Decisions

- **Sequential Python modules, not multi-process.** Each agent is a function (or small set of functions) invoked in order from a daily orchestrator script. No queue, no IPC, no async. v1 is small enough that this is the right call; revisit only if a single agent grows past what a single process can do in a reasonable wall-clock window.
- **SQLite is the only persistence layer.** No Redis, no Postgres, no vector store in v1. If a slice needs richer querying, justify it then.
- **Paper mode only.** The system never executes trades. The user receives a report, decides what to do, and reports back fills/skips through the feedback path.
- **Web UI is a thin presentation/feedback surface, not a fifth agent.** It is read-only over agent tables and routes all writes through `traders.feedback`. It ships behind the `web` extra so the core install stays dependency-free, and binds to `127.0.0.1` (single-user, local). Form writes are CSRF-protected.
