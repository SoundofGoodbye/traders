# Slices

The build plan for `traders`. Each slice is a self-contained increment — propose and ship one at a time.

**Status: slices 0–17 are shipped.** The `Future` section at the bottom lists deferred ideas, not committed work.

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

## Slice 14 — Strategy goal + scorer

- Closes the "well-defined goal" gap: success and failure become numbers, not vibes.
- `traders.strategy` loads a `StrategyGoal` (target 30-day return, max drawdown, min hit-rate, min Sharpe proxy, min-closed-for-verdict). Layered load: operator override at `data/strategy.json` wins, else the packaged `strategy.default.json`. JSON not YAML — the core install has zero dependencies.
- `traders.metrics` computes realized metrics over closed positions (trailing-30d return, max drawdown, hit rate, per-trade Sharpe proxy) and scores them criterion-by-criterion into a `ScoreCard` with verdict `on_track` / `failing` / `insufficient_data`. Pure functions; one SELECT, no mutation.
- `sharpe_per_trade` is mean(PnL)/stdev(PnL) across closed trades — a proxy, explicitly **not** an annualized Sharpe ratio.
- `traders metrics` CLI subcommand renders the scorecard via `reports.render_metrics` (`--format {text,markdown}`, `--output PATH`), mirroring `pm` / `review`.
- No schema changes, no new dependencies.

## Slice 15 — Tunable parameters

- Externalizes the agent knobs that were hardcoded so a later optimizer can tune them without code edits.
- `traders.parameters` loads `LearnedParameters` — scoped to the knobs that actually exist in the pipeline today: Scout's `batch_size` and the PM's `max_total_size_pct`. (The Analyst has no numeric knob of its own yet; its conviction/sizing come from the thesis generator. More knobs slot in as real generators/signals land.) Same layered load as the goal: `data/learned_parameters.json` override wins, else the packaged `parameters.default.json`, whose values equal the previous in-code defaults so behavior is unchanged.
- Scout / Portfolio / the orchestrator accept an optional `params` argument and fall back to `load_parameters()` when neither it nor an explicit value is given; explicit `batch_size` / `max_total_size_pct` still override. CLI defaults became `None` so the learned values flow through `run-daily`.
- `traders params` CLI subcommand prints the active parameters.
- No schema changes, no new dependencies.

## Slice 16 — Self-improvement loop (Optimizer)

- Closes the loop the video is really about: read outcomes, propose **one** change, gate it behind human approval, keep an auditable trail.
- Migration `005_experiments.sql` adds an append-only `experiments` table (hypothesis, param, old/new value, baseline verdict + metrics JSON, status `proposed`/`applied`/`rejected`, timestamps).
- `traders.optimizer` scores the current strategy (slice 14), and if not already `on_track`, an `Optimizer` (stub v1, protocol-pluggable like `PostMortemGenerator`) proposes a single-variable change to `learned_parameters.json` following the scientific method — the failing criterion picks the knob. The proposal is **logged only**; nothing mutates parameters.
- `traders optimize` lists/creates proposals (read-only by default); `traders optimize --apply N` writes the one approved change to `data/learned_parameters.json` and marks the experiment `applied`; `--reject N` marks it `rejected`. Human-in-the-loop, paper-only, exactly like the video's "first cycle is review-only, flip to live when ready."
- No new dependencies.

## Slice 17 — Backtest harness

- Closes the gap that made slice 16 mostly theoretical: live paper-trading closes a handful of positions per week — far too sparse to converge the Optimizer one variable at a time. The harness replays a parameter set over historical prices so a change is scored against months of data in seconds.
- `traders.backtest` composes the **pure decision primitives** the live agents already expose — `scout.filter_candidates`, the `ThesisGenerator`, `portfolio.evaluate`, `post_mortems.compute_pnl_pct`, `metrics.score` — so the simulation can't silently diverge from production. It runs entirely in memory and **never writes to the live tables**.
- Model: walk rebalance dates (`rebalance_every_days`) from `start` to `end`; at each, close positions whose holding period elapsed (as-of close), run filter→generate→evaluate against the still-open sim positions, and open each accepted pick at the as-of close with `exit_day = today + holding_days`. Realize leftovers at the end; score the realized trades with the existing goal + metrics.
- `traders.prices` adds a `PriceHistory` (as-of close lookup), a deterministic `synthetic_history` (hashlib-seeded — hermetic, zero-setup demos/tests), and a DB-backed store (`migrations/006_prices.sql`, `load_history_from_db` / `save_prices`). Populating the store from a real provider is deferred to the improvement plan.
- `metrics.metrics_from_trades` is extracted as a pure aggregator so the harness reuses the exact metric math; `compute_metrics` delegates to it (behavior unchanged).
- `compare_params` and `backtest_experiment` evaluate a slice-16 proposal — current params vs the one proposed change — over the same history, so the Optimizer's suggestions become testable against history instead of a few live trades.
- `traders backtest` CLI: `--source {synthetic,db}`, `--start/--end`, `--holding-days`, `--rebalance-days`, param overrides, `--compare-experiment ID`, `--format {text,markdown}`, `--output`. No new dependencies (stdlib only); migration is append-only; default `uv run pytest` stays hermetic.

## Proposed — improvement plan (slices 18+)

Full rationale, ordering, and premortem in [improvements.md](improvements.md). The
theme: turn the signal-free pipeline (date-rotation Scout, canned Analyst) into one
that actually uses data, without breaking the zero-dep core or hermetic tests.
Implemented slices below get promoted to a shipped `## Slice N` section above.

- **Slice 18 — Signals library** (`traders.signals_lib`, CORE/hermetic): pure-stdlib, look-ahead-safe price signals (momentum, realized vol, mean-reversion z-score, RSI) + cross-sectional helpers. Foundation for 20/21/22.
- **Slice 19 — Stooq price ingestor** (CORE/zero-dep): `urllib`+`csv` fetcher (injected in tests) + ticker→Stooq symbol map; `traders ingest-prices` fills the `prices` table.
- **Slice 20 — `SignalThesisGenerator`** (CORE, `ThesisGenerator`): maps signals onto real `DraftThesis` fields; replaces the canned stub.
- **Slice 21 — `RankingScout`** (CORE): ranks the watchlist by a composite signal from `prices`; falls back to date-rotation when prices are absent.
- **Slice 22 — Backtest rigor** (CORE): in-sample/out-of-sample split, transaction-cost/slippage parameter, optional next-bar (entry-lag) fills.
- **Slice 23 — Optimizer rigor** (CORE): OOS-improvement gate + trial-count deflation (PSR/DSR) so the optimizer can't overfit; still human-`--apply` gated.
- **Slice 24 — Fundamentals ingestion** (`realdata`): `fundamentals` table (migration 007) + loader; fuels 25.
- **Slice 25 — Fundamental & catalyst signals** (CORE): value/quality (Piotroski) + PEAD/earnings-proximity, wired into 18/20.
- **Slices 26–28 — LLM path** (`llm` extra): `LLMThesisGenerator` / `LLMPostMortemGenerator` via structured-output tool calls + an eval harness; hermetic via an injected fake client.

## Future

- Additional data sources (FMP, Polygon, paid news APIs) behind the same `DataSource` protocol.
- HTML rendering on top of the slice 7 markdown surface, if/when wanted.
- Cross-run data caching if rate limits start mattering.
