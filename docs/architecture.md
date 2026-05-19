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
[Feedback CLI]     →  positions, feedback
   ↓
[Reviewer, weekly] →  post_mortems
```

## Decisions

- **Sequential Python modules, not multi-process.** Each agent is a function (or small set of functions) invoked in order from a daily orchestrator script. No queue, no IPC, no async. v1 is small enough that this is the right call; revisit only if a single agent grows past what a single process can do in a reasonable wall-clock window.
- **SQLite is the only persistence layer.** No Redis, no Postgres, no vector store in v1. If a slice needs richer querying, justify it then.
- **Paper mode only.** The system never executes trades. The user receives a report, decides what to do, and reports back fills/skips through the feedback path.
