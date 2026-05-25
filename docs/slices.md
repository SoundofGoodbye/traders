# Slices

The build plan for `traders`. Each slice is a self-contained increment — propose and ship one at a time.

## Slice 0 — scaffold

- `pyproject.toml`, `src/traders/`, `tests/`, `migrations/001_initial.sql`, baseline docs.
- Smoke test (`tests/test_smoke.py`) passes under `uv run pytest`.
- No agent logic.

## Slice 1 — Scout

- Hardcoded watchlist loaded from a JSON file: S&P 100 + EuroStoxx 50 tickers.
- Scout agent filters the watchlist into a small candidate set per run; writes to `candidates`.
- Deterministic at this stage — heuristics, not LLM.

## Slice 2 — Researcher

- Per-candidate deep dive. Writes a structured note to `research_notes` (free-form `content` plus a `sources` field).
- First slice that calls out to external data (filings, recent news). Sources tracked.

## Slice 3 — Analyst

- Produces theses with `thesis_type` (value / catalyst / momentum / mean-reversion), `direction`, `conviction` (1–5), `suggested_size_pct`, `exit_condition`, `rationale`.
- One row per thesis in `theses`. Open status by default.

## Slice 4 — Portfolio Manager

- Final filter over the day's theses. Concentration / correlation check against open `positions`.
- Generates a daily report (rendering shipped in slice 7).

## Slice 5 — Reviewer (weekly)

- Walks closed positions, writes `post_mortems` with outcome and lessons.
- Lessons feed into prompts for Analyst/Researcher in later iterations.

## Slice 6 — Feedback loop

- User reports fills, partials, skips, sells back through a small CLI / API surface.
- `feedback` rows flow into `positions` updates.

## Slice 7 — Report rendering

- Markdown renderers for the PM's daily report and the Reviewer's weekly run.
- `pm` and `review` gain `--format {text,markdown}` and `--output PATH` flags; default `text` preserves the legacy stdout summary.
- Pure transformation layer (`traders.reports`); no schema changes, no new dependencies.

## Slice 8 — Orchestrator (cron-ready)

- `traders.orchestrator` chains Scout → Researcher → Analyst → PM behind one `run_daily(conn, ...)` call; Reviewer runs behind `run_weekly(conn)`. Fail-fast: any agent error propagates.
- `run-daily` and `run-weekly` CLI subcommands; `--format {text,markdown}` and `--output PATH` mirror `pm`/`review`. Markdown to stdout suppresses per-step progress so it stays pipeable; markdown-to-file (or text) keeps progress visible.
- No schema changes, no new dependencies — pure composition over the existing agents. Cron wiring is left to the operator.

## Slice 9 — yfinance data source

- First real `DataSource` implementation. yfinance is the low-friction pick: no API key, covers both the S&P 100 and EuroStoxx 50 tickers, and returns news + fundamentals which slot directly onto the existing `DataPoint` kinds.
- Opt-in via `--data-source {stub,yfinance}` on `research` and `run-daily`; default stays `stub` so existing tests remain hermetic.
- Optional install — `uv sync --extra realdata` pulls in yfinance; the default install footprint stays empty.
- The adapter returns `[]` on any network or parse error (honoring the protocol contract that no data is not an exception), so a flaky ticker can't kill the run.
- No caching, no retries, no rate-limit handling — operator runs `run-daily` once per close, which is well within yfinance's tolerances.
- yfinance does not serve filings; `filing` kind is left empty pending a later EDGAR adapter.

## Slice 10+

- Additional data sources (FMP, Polygon, SEC EDGAR for filings) behind the same `DataSource` protocol.
- HTML rendering on top of the slice 7 markdown surface, if/when wanted.
- Cross-run data caching if rate limits start mattering.
