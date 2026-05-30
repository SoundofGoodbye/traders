# Slices

The build plan for `traders`. Each slice is a self-contained increment — propose and ship one at a time.

**Status: slices 0–13 are shipped.** The `Future` section at the bottom lists deferred ideas, not committed work.

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

## Slice 10 — SEC EDGAR data source

- Closes the `kind="filing"` gap Slice 9 left open. `EdgarDataSource` emits one DataPoint per recent 10-K / 10-Q / 8-K (forms and limit configurable) from SEC EDGAR's submissions JSON.
- Opt-in via `--data-source edgar` on `research` and `run-daily`; default stays `stub` so existing tests remain hermetic.
- Two-step EDGAR shape (ticker → CIK map, then per-CIK submissions JSON) hidden behind one `filings_fn(ticker) -> list[dict]`; tests inject the fetcher to stay network-free.
- The default fetcher hard-requires `TRADERS_EDGAR_UA` — a real contact string like `"Acme Research user@acme.com"`. SEC blocks anonymous traffic, so an unset env var raises `RuntimeError` at construction. Mirrors the yfinance missing-package pattern: fail fast at the boundary, not deep inside a fetch.
- Errors return `[]` per the `DataSource` contract — a 429 or DNS failure can't kill the Researcher run.
- No new dependencies — stdlib `urllib.request` + `json`. No caching, no retries, no rate-limit handling; once-per-close cadence stays well inside SEC's tolerances.
- Pairs naturally with `yfinance`: agents read whichever backend is configured through the same `DataSource` protocol, with no awareness of either.

## Slice 11 — Local web UI: skeleton + Today + Positions pages

- First UI surface for the system; read-only, server-rendered, runs locally. No React, no frontend build step, no npm.
- New `traders.web` package: FastAPI app, Jinja2 templates under `traders/web/templates/`, plain inline CSS — no framework.
- Read layer (`traders.web.queries`) wraps the existing `sqlite3` connection with typed read functions; agents keep their own write paths and the UI never mutates state in this slice.
- `/` (Today) renders the latest daily report — candidates → theses → PM picks — surfacing conviction, size, direction, exit, and rationale.
- `/positions` lists open and recently-closed positions; unrealized P&L is computed when a `DataSource` is configured, otherwise the price/P&L columns show `—`.
- `traders web` CLI subcommand boots `uvicorn` on a configurable host/port; mirrors the shape of `run-daily` / `run-weekly`.
- Tests cover the read layer against a seeded in-memory DB plus smoke tests asserting every route returns 200.
- New dependencies (`fastapi`, `uvicorn`, `jinja2`) land behind a `web` extra so the default install footprint stays untouched.

## Slice 12 — Theses detail + Reviews pages

- `/theses` lists open and historical theses with query-param filters: ticker, date range, min conviction. Pure server-side filtering — no JS.
- `/theses/{id}` shows the full thesis (rationale, exit, sizing) alongside the originating research note(s) and their `sources` field, with a backlink to the PM run that surfaced it.
- `/reviews` lists the Reviewer's weekly post-mortems newest first; each row links to the closed position(s) it covered.
- Reuses the Slice 11 read layer and template conventions; new read functions land in `traders.web.queries` rather than a per-page DB module.
- Read-only — no writes anywhere. Tests follow the Slice 11 pattern: query-layer tests against a seeded DB plus a smoke test per route.
- No schema changes, no new dependencies.

## Slice 13 — Feedback actions in the UI

- First write surface in the web UI. Buttons/forms on `/positions` (and relevant detail pages) replace the Slice 6 feedback CLI for the common actions: report fill, partial, skip, sell.
- POST endpoints in `traders.web` call the existing Slice 6 feedback functions directly — no duplicated business logic, no parallel write path into `positions`.
- CSRF protection via a per-session token stored in a signed cookie; tokens are required on every POST. Single-user assumption — no login, no accounts.
- The Slice 6 CLI continues to work unchanged; the UI is an alternative entry point, not a replacement.
- Tests cover the POST handlers (happy path + CSRF rejection) and assert the resulting `positions` row matches what the CLI would have written for the same input.
- No schema changes, no new dependencies beyond Slice 11's `web` extra.

## Future

- Additional data sources (FMP, Polygon, paid news APIs) behind the same `DataSource` protocol.
- HTML rendering on top of the slice 7 markdown surface, if/when wanted.
- Cross-run data caching if rate limits start mattering.
