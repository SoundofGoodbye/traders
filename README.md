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
uv run traders scout       # Scout → candidates
uv run traders research    # Researcher → research_notes
uv run traders analyse     # Analyst → theses
uv run traders pm          # Portfolio Manager → pm_decisions + report
uv run traders review      # Reviewer → post_mortems  (weekly)
uv run traders run-daily   # Scout → Researcher → Analyst → PM in one shot
uv run traders run-weekly  # Reviewer in one shot
```

Each subcommand takes `--db PATH` and (where applicable) a `--*-run-id` flag to target a specific upstream run instead of the latest. `research` and `run-daily` additionally take `--data-source {stub,yfinance}` (default `stub`); see [Data sources](#data-sources) below. `pm`, `review`, `run-daily`, and `run-weekly` additionally take `--format {text,markdown}` (default `text`) and `--output PATH` to write the rendered document to a file instead of stdout:

```bash
uv run traders pm --format markdown                       # print daily report as markdown
uv run traders pm --format markdown --output report.md    # write daily report to a file
uv run traders review --format markdown --output week.md  # weekly review as markdown
uv run traders run-daily --format markdown > report.md    # full daily chain, clean pipeable markdown
```

`run-daily` prints per-step progress (`scout run N: X candidate(s)`, `research run N: …`, `analyst run N: …`, `pm run N: …`) for the text format and when writing markdown to a file; when emitting markdown to stdout it suppresses progress so the output stays a single clean markdown document. Agent errors propagate — no partial-success swallowing.

### Feedback

After the PM emits the daily report, the user executes manually and reports back:

```bash
uv run traders feedback fill    --thesis-id N --price P [--size-pct S] [--notes "..."]
uv run traders feedback partial --thesis-id N --price P --size-pct S    [--notes "..."]
uv run traders feedback skip    --thesis-id N                            [--notes "..."]
uv run traders feedback sell    (--position-id N | --thesis-id N) --price P [--notes "..."]
```

`fill` / `partial` open a position; `sell` closes one; `skip` is a log-only record that the user declined the suggestion. Each event also writes a row to `feedback` carrying the price, size, and the position it opened or closed.

## Data sources

The Researcher reads evidence through a `DataSource` protocol; the default is `StubDataSource` (deterministic fixture data, no I/O). Slice 9 added an opt-in `YFinanceDataSource` that pulls news and fundamentals from Yahoo Finance:

```bash
uv sync --extra realdata                                # installs yfinance
uv run traders research --data-source yfinance          # research using real data
uv run traders run-daily --data-source yfinance         # full chain with real data
```

The default install does **not** include yfinance — keep the footprint empty unless you opt in. The CLI default is `--data-source stub` so existing behavior and the test suite stay hermetic.

The adapter degrades gracefully: network errors, rate limits, or missing fields produce an empty list of data points rather than raising, so a single flaky ticker can't kill the run. Filings are not covered yet (yfinance doesn't serve them — a SEC EDGAR adapter is owned by slice 10+). There is no caching layer — once per close is well within yfinance's tolerances.

## Current limitations

- **Stub generators in place of LLMs.** `StubThesisGenerator` and `StubPostMortemGenerator` ship today. The `ThesisGenerator` / `PostMortemGenerator` protocols are stable; real model-backed implementations drop in behind them in later slices.
- **Scout heuristic is a date rotation**, not a data-driven filter. Real signals will arrive once Scout is rewired to consume from a `DataSource` itself (a follow-up slice — Researcher is the first consumer today).
- **No in-process scheduler.** `run-daily` and `run-weekly` chain the agents end-to-end, but timing (post-close daily, weekly review) is left to the operator — wire them to cron, systemd timers, or whatever the host runs.
- **yfinance only, no caching.** Real data today comes from yfinance; FMP, Polygon, and EDGAR are slice 10+. No cross-run cache yet — if rate limits start to bite, that's the trigger to add one.

## Running

```bash
uv sync
uv run pytest
```

## Layout

- `src/traders/` — package source (one module per agent plus `db`, `cli`, `signals`, `data_sources`, `post_mortems`, `feedback`, `reports`, `orchestrator`)
- `tests/` — pytest suite
- `migrations/` — SQLite schema migrations, applied in order; append-only
- `data/` — local SQLite db (gitignored)
- `docs/` — slice plan, architecture, glossary

See `docs/slices.md` for the build order and `docs/architecture.md` for the agent shape.
