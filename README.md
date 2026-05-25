# traders

Agent-driven stock research and advisory system. Daily cadence over an S&P 100 + EuroStoxx 50 watchlist, paper mode only — the system never places orders; the user executes manually and reports fills back through a feedback path. Built as plain Python modules sharing a SQLite database.

## Agents

- **Scout** — filters the watchlist down to a small daily candidate set. Deterministic date-seeded rotation today; data-driven later.
- **Researcher** — per-candidate digest of filings, news, fundamentals into `research_notes` with cited sources.
- **Analyst** — turns each note into zero-or-more theses (type, direction, conviction, suggested size, exit condition).
- **Portfolio Manager** — final filter over the day's theses against open positions; persists per-thesis accept/reject decisions and emits a daily summary.
- **Reviewer** (weekly) — walks closed positions and writes post-mortems.

The first four run sequentially post-close; Reviewer runs weekly.

## CLI

```bash
uv run traders scout      # Scout → candidates
uv run traders research   # Researcher → research_notes
uv run traders analyse    # Analyst → theses
uv run traders pm         # Portfolio Manager → pm_decisions + report
uv run traders review     # Reviewer → post_mortems  (weekly)
```

Each subcommand takes `--db PATH` and (where applicable) a `--*-run-id` flag to target a specific upstream run instead of the latest.

## Current limitations

- **Stub generators in place of LLMs.** `StubThesisGenerator`, `StubPostMortemGenerator`, and `StubDataSource` ship today. The `ThesisGenerator` / `PostMortemGenerator` / `DataSource` protocols are stable; real model- and API-backed implementations drop in behind them in later slices.
- **No feedback CLI yet (slice 6).** `positions` and `feedback` tables exist, but nothing writes to them from the CLI. To exercise Reviewer end-to-end you currently have to insert closed `positions` rows by hand.
- **Scout heuristic is a date rotation**, not a data-driven filter. Real signals arrive once a real data source lands (slice 7+).
- **No report rendering and no scheduler.** PM emits its summary to stdout; daily/weekly orchestration is still manual.

## Running

```bash
uv sync
uv run pytest
```

## Layout

- `src/traders/` — package source (one module per agent plus `db`, `cli`, `signals`, `data_sources`, `post_mortems`)
- `tests/` — pytest suite
- `migrations/` — SQLite schema migrations, applied in order; append-only
- `data/` — local SQLite db (gitignored)
- `docs/` — slice plan, architecture, glossary

See `docs/slices.md` for the build order and `docs/architecture.md` for the agent shape.
