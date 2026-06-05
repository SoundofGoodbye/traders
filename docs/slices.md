# Slices

The build plan for `traders`. Each slice is a self-contained increment — propose and ship one at a time.

**Status: slices 0–41 are shipped.** Slices 18–29 completed the improvement plan; slices 30–32 added the Tiingo price source, a `.env` config loader, and job control in the web UI; slices 33–36 (from the persona-review [backlog](backlog.md): items B1, B2, B4, B3) add period-by-period fundamentals ingestion, a Piotroski quality score over them, and a two-leg gate on the value thesis — quality (no cheap-but-deteriorating "value traps") and a margin of safety to intrinsic value (no cheap-but-fully-priced names) — with margin of safety driving conviction; slices 37–38 (item B5) add a user buy-list with price triggers and its `/buy-list` web page; slices 39–40 (items B8, B9) surface honest data caveats at the point of claim and reframe the Today page to dampen the daily-action reflex. The `Future` section at the bottom lists deferred ideas, not committed work.

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

## Slice 18 — Signals library

`traders.signals_lib`: pure-stdlib, look-ahead-safe price signals (12-1 momentum, realized vol, 20-day mean-reversion z-score, Wilder RSI) plus cross-sectional `winsorize`/`zscore`. `closes_before(history, ticker, as_of)` is the single look-ahead gate. Leaf module; foundation for 20/21/22.

## Slice 19 — Stooq price ingestor

Fills the `prices` table from Stooq — free, US+EU, stdlib-only (`urllib`+`csv`), so it stays in the zero-dep core. `stooq_symbols` maps Yahoo-style tickers (`MC.PA`→`mc.fr`, `BRK.B`→`brk-b.us`); `price_ingest` uses an injected fetcher (tests) + idempotent `save_prices`; `traders ingest-prices`. Documents the unadjusted-close/survivorship caveats.

## Slice 20 — SignalThesisGenerator

Replaces the canned stub behind the `ThesisGenerator` protocol: maps slice-18 signals onto real `DraftThesis` fields (momentum / mean-reversion, vol-scaled size, per-type exits), long-only, look-ahead-safe. Opt-in via `--generator signals` on `analyse` / `run-daily`.

## Slice 21 — RankingScout

`scout.rank_candidates` ranks the watchlist by a composite (momentum + oversold) cross-sectional signal from `prices`; `scout.run(history=...)` uses it with a rotation fallback when no name has signals. Opt-in via `--rank signals`.

## Slice 22 — Signal strategy in the backtest

`run_backtest(use_signals=True)` replays the real ranked-Scout + signal-thesis strategy, recomputed as-of each rebalance date, so backtests / `compare_params` / `backtest_experiment` measure slices 20–21 rather than the rotation+stub placeholder. `traders backtest --strategy signals`.

## Slice 23 — Backtest realism + train/test split

Per-trade transaction costs (`cost_bps`), entry-lag (next-bar) fills (`entry_lag_days`), and `split_backtest` (in-sample / out-of-sample) to expose overfitting. `traders backtest --cost-bps --entry-lag-days --oos-fraction`. The premortem's must-not-skip realism guardrails.

## Slice 24 — Optimizer OOS gate + trial deflation

Gates `optimize --apply` on an **out-of-sample** backtest so the slice-16 Optimizer can't bless a change that only looks good in-sample. `backtest.gate_experiment` runs `split_backtest` for the baseline and the candidate (baseline + the one proposed change) and applies a two-part rule (`decide_oos_gate`): the candidate must (1) beat the baseline's out-of-sample per-trade Sharpe proxy **and** (2) clear a minimum **Deflated Sharpe Ratio** for the number of proposals tried. `traders.deflated_sharpe` (pure stdlib, `statistics.NormalDist`) implements the Probabilistic / Deflated Sharpe Ratio (Bailey & López de Prado): `expected_max_sharpe` is the multiple-testing benchmark the proxy must clear, so more proposals (`optimizer.count_experiments`) raise the bar — the optimizer can't fish. `traders optimize --apply` now refuses an un-improving proposal (printing a gate report); `--force` restores the old unconditional apply, and `--source/--strategy/--start/--end/--oos-fraction/--min-dsr/--params/--format/--output` tune it. Defaults to real ingested prices (`--source db`) and warns when gating on the synthetic source. The human `--apply` gate is unchanged; no migration, no new dependencies.

## Slice 25 — Fundamentals ingestion

`traders.fundamentals` + migration `007_fundamentals.sql`: point-in-time fundamental snapshots per ticker (`market_cap`, `trailing_eps`, `book_value_per_share`, `free_cash_flow`, `shares_outstanding`, `next_earnings_date`) — the raw inputs the slice-26 value / quality / catalyst signals derive from. `ingest_fundamentals` hides the network behind an injected `fetch_fn` (real impl = yfinance `.info`, behind the `realdata` extra; tests inject canned info), mirroring the Stooq ingestor. `save_fundamentals` / `load_fundamentals` are idempotent on `(ticker, as_of, source)`, and `latest_fundamentals(..., as_of=...)` is the look-ahead-safe accessor slice 26 reads. Ratios aren't stored — slice 26 joins a snapshot against the `prices` close so it can't bake in a stale price. `traders ingest-fundamentals`. Caveat (documented): a yfinance `.info` reading is a *current* snapshot stamped with an `as_of` date, not a historical series — true historical fundamental backtests need period-by-period statements, deferred. No new dependency (reuses `realdata`); hermetic by default via the injected fetcher.

## Slice 26 — Fundamental & catalyst signals

Adds fundamental signal math to `signals_lib` and wires it into the `SignalThesisGenerator`. Value signals — `earnings_yield` (E/P), `book_to_price` (B/P), `fcf_yield` (FCF/market-cap) — join a look-ahead-safe slice-25 snapshot against the last close before the decision day; a name cheap on ≥2 of the three flags yields a long `value` thesis. `days_to_earnings` annotates whatever thesis fires with an event-risk note when earnings are imminent (≤7d) — a proximity flag, not a directional call. The generator gains an optional `fundamentals` map (resolved per as-of by `fundamentals.load_fundamentals_asof`); family priority is momentum → value → mean-reversion. **Additive by construction:** with no fundamentals (every historical backtest — snapshots aren't point-in-time history — and the default tests) behaviour is byte-identical to slice 20. `traders analyse --generator signals` / `run-daily --generator signals` pick up ingested fundamentals automatically. Deferred (not computable from a single snapshot): the full **Piotroski F-score** (needs period-by-period statements) and **PEAD/SUE** (needs consensus estimates). No migration, no new dependency.

## Slice 27 — LLMThesisGenerator

`traders.llm_thesis.LLMThesisGenerator` implements the `ThesisGenerator` protocol — it drops into the Analyst like the stub/signal generators (no agent changes) and asks Claude to read the research note and propose a thesis via a **forced structured-output tool call** (`tool_choice` → `record_thesis`), so the response is always a valid `DraftThesis` or an explicit decline (`actionable=false`), never free text. The static system prompt is sent as a cached block (`cache_control: ephemeral`). Behind the optional `llm` extra (`anthropic`); the client is **injected**, so the default suite stays hermetic — tests pass a fake client (no `anthropic` import, no API key, no network) and only the real default client imports the SDK and reads `ANTHROPIC_API_KEY` (failing fast if the extra is missing, returning `[]` on a per-call API error). Security: the untrusted note is wrapped in `<research_note>` delimiters with a system instruction never to follow its contents, the tool-only response constrains output to the schema, the result is validated/clamped in Python, and the paper-only / human `--apply` gates stay in force. `traders analyse --generator llm` / `run-daily --generator llm`; `TRADERS_LLM_MODEL` overrides the model. No migration.

## Slice 28 — LLMPostMortemGenerator

`traders.llm_postmortem.LLMPostMortemGenerator` implements the `PostMortemGenerator` protocol — the Reviewer's weekly write-up, Claude-backed, on the same pattern as slice 27: a forced structured-output tool call (`record_post_mortem` → `{outcome, lessons}`), prompt caching on the static system prompt, and the untrusted-text defense applied to the thesis's free-text fields (a rationale may itself trace back to ingested news/filings). The shared injected/lazy-client + model plumbing was extracted to `traders.llm` (`LLMGenerator`) this slice, so both LLM generators reuse it. Unlike the thesis generator, the protocol must **always** return a draft, so a model/parse error or empty output falls back to a deterministic one (the stub's price-derived outcome line + a "write-up unavailable" lessons note) — a flaky call can't fail the weekly run; a missing `llm` extra still fails fast. `traders review --generator llm` / `run-weekly --generator llm`; `TRADERS_LLM_MODEL` overrides the model. No migration.

## Slice 29 — Eval harness for the LLM generators

`traders.eval_llm`: a pure-stdlib, generator-agnostic harness that scores an LLM generator against fixture cases, so a prompt or model change that quietly degrades quality gets caught. Each case pairs an input with `Check` predicates over the output; `evaluate_thesis_generator` / `evaluate_post_mortem_generator` run the generator, apply the checks (a check that raises counts as a failure, never a crash), and aggregate an `EvalReport` (per-case pass/fail + overall pass rate). `default_thesis_cases` / `default_post_mortem_cases` are the golden fixtures (a clear-value note → expect a thesis; a no-edge note → expect a decline; a winner and a loser post-mortem). Hermetic: the harness imports no `anthropic`, so tests drive it with fake generators (or a real generator wired to an injected fake client). `traders eval-llm [--generator thesis|postmortem|both] [--min-pass-rate R]` runs the golden cases against the real Claude generators — the actual regression check — and exits non-zero below `--min-pass-rate`, so it works as a CI gate. This slice also made the `--generator llm` CLI paths exit cleanly (not traceback) when the `llm` extra is missing. No migration.

## Improvement plan: complete

All data-collection / predictive-analysis slices (18–29) from [improvements.md](improvements.md) are shipped: the deterministic signal stack (18–22), backtest/optimizer rigor (23–24), fundamentals (25–26), and the LLM path (27–29). Slice 30 below is a post-plan addition.

## Slice 30 — Tiingo price source

Stooq gated its free CSV endpoint (it now returns an apikey/captcha prompt instead of data), which broke the slice-19 ingestor's real-data path — it degraded gracefully (skipped every ticker, never crashed) but collected nothing. Slice 30 makes `ingest_prices` source-agnostic (an injectable `parse`, plus a `price_source(name)` selector returning `(fetch_csv, parse, symbol_map)`) and adds `traders.tiingo`: split/dividend-**adjusted** EOD closes (the right input for return-based signals) parsed from Tiingo's CSV (`adjClose`), behind the same injected-fetcher pattern so tests stay hermetic. The fetcher hard-requires `TIINGO_API_KEY` (free key; fail-fast at the boundary like the EDGAR adapter); Tiingo's free tier is US EOD so foreign-venue suffixes map to `None` (skipped). `traders ingest-prices --source {tiingo,stooq}` now defaults to `tiingo` and exits cleanly with the key hint when the token is missing. No migration, no new dependency (stdlib `urllib`+`csv`).

## Slice 31 — .env config loader

`traders.env.load_dotenv` — a minimal, zero-dependency `.env` reader (no `python-dotenv`) that the CLI calls once at startup, so secrets like `TIINGO_API_KEY`, `TRADERS_EDGAR_UA`, and `ANTHROPIC_API_KEY` live in one gitignored `./.env` instead of being exported by hand each shell. Deliberately conservative: **set-if-absent** (an exported var always wins, and the suite stays hermetic), missing file is a no-op, and it skips blanks / `#` comments / a leading `export` / quote-wrapped values. A committed `.env.example` documents every recognized key; `.env` is gitignored. No new dependency.

## Slice 32 — Job control in the web UI

`traders.jobs` is the small control surface the cron wrappers and the UI share: a per-job **enabled** flag in `<data_dir>/jobs.json`, and **last-run** status parsed from `cron.log`. The runner scripts call `traders jobs check NAME` and skip when a job is off, so toggling in the UI stops the work **without editing the crontab** — no command execution from the browser. `traders jobs {status,enable,disable,check}` exposes the same on the CLI. A new `/jobs` page (CSRF-protected, like the feedback writes) lists each job's schedule, on/off state, last run + status, and a tail of `cron.log`, with a button to flip each job. The jobs config lives beside the db (so the UI and cron agree), keeping web tests hermetic. No migration; reuses the slice-11/13 web layer + `web` extra.

## Slice 33 — Period-by-period fundamentals

First step of [backlog](backlog.md) item **B1** and the foundation for the
fundamental-depth cluster (B2 quality/Piotroski, B3 margin-of-safety) the
persona review flagged as credibility-critical: the slice-25 `fundamentals` table
is a single current `.info` snapshot stamped with an `as_of` date, which can't
support a quality score or a true value case. `traders.fundamental_periods` +
migration `008_fundamental_periods.sql` add a point-in-time **series** — one row
per `(ticker, period_end, period_type, source)` holding the raw income /
balance-sheet / cash-flow lines (revenue, gross profit, net income, operating cash
flow, capex, total/current assets & liabilities, long-term debt, equity, shares)
that Piotroski / ROIC / margin-trend / owner-earnings work needs.

Mirrors the slice-25 shape exactly: a typed `FundamentalPeriod`, idempotent
`save_periods` / `load_periods`, and `ingest_fundamental_periods` behind an
injected `fetch_fn` (real impl = yfinance statements behind the `realdata` extra;
tests inject canned per-period dicts, so the default suite stays hermetic and
offline). **Look-ahead safety** (premortem guardrail #3) is built in from day one:
a statement isn't public until it's *filed*, so each row carries an optional
`available_at` filing date, and `availability_date` / `load_periods_asof` /
`latest_periods` gate on filing date — falling back to a conservative
`period_end` + reporting-lag estimate (90d annual / 45d quarterly) when no filing
date is known — so a historical rebalance can't peek at a not-yet-filed period.
Ratios are not stored (derived figures join the raw lines, and the `prices` close
where a price is needed). `traders ingest-fundamental-periods`. Append-only
migration; no new dependency (reuses `realdata`). The consuming quality/value
signals are the next slices (B2/B4); this slice only ingests, stores, and exposes
the look-ahead-safe series.

## Slice 34 — Piotroski F-score quality screen

[Backlog](backlog.md) item **B2** and the first consumer of the slice-33 series:
a deterministic, look-ahead-safe **quality score** that the value path will gate
on (B4), so a "cheap" name must also be improving and financially sound rather
than a value trap — the persona review's sharpest criticism. Mirrors the
slice-18-before-20 shape: pure computation now, wiring next.

`traders.quality` computes Piotroski's nine binary tests over a ticker's current
vs prior **annual** period — profitability (ROA positive, operating cash flow
positive, ROA improving, accruals: CFO > net income), leverage/liquidity
(long-term-debt-to-assets falling, current ratio rising, no share dilution), and
operating efficiency (gross margin rising, asset turnover rising). Each test
returns `None` when its inputs are missing, so a sparse statement yields a
*partial* score with a known denominator (`PiotroskiScore.score` /
`.computable`) instead of a silently wrong 0-of-9 — a consumer can demand both a
minimum score and a minimum number of computable tests. `piotroski_for(conn,
ticker, as_of=...)` pulls the two most recent annual periods *available as of* the
decision date via `fundamental_periods.latest_periods`, inheriting the slice-33
look-ahead gate (a historical rebalance can't use a statement filed later);
quarterly rows are ignored. Pure stdlib, no migration, no new dependency; fully
hermetic. **Next (B4):** gate the `SignalThesisGenerator` value thesis on the
score (cheap *and* quality-pass), and fold it into conviction. ROIC/ROE trend and
interest coverage (the rest of B2) can extend `quality` on the same pattern.

## Slice 35 — Quality gate on the value thesis

[Backlog](backlog.md) item **B4** — the slice that makes the fundamental-depth
work user-visible and closes the persona review's sharpest criticism: the value
thesis no longer fires on cheapness alone (a single-snapshot value-trap
generator). The `SignalThesisGenerator` gains an optional `quality` map
(per-ticker `PiotroskiScore`, resolved look-ahead-safe by
`quality.quality_scores_asof`), and `_value_thesis` consults it:

- a cheap name with a **confirmed weak** F-score (`computable ≥ 5` and
  `score < 5`) is **vetoed** — no thesis (the value-trap filter);
- a **confirmed strong** score (`≥ 7`) **bumps conviction** by one (capped at 5)
  — folding quality into conviction, per the review's "conviction should mean
  something" note;
- an **unknown or too-sparse** score (no entry, or `computable < 5`) **falls back**
  to the slice-26 cheap-only thesis.

**Additive by construction**, exactly like the slice-26 fundamentals path: with no
`quality` map (every historical backtest, and the default tests) behaviour is
byte-identical to slice 26, so nothing regresses. The passing score is recorded in
the rationale (`Quality: Piotroski N/9 (M tests)`), and the plain-English layer
(`web.explain`) gains a Piotroski glossary entry plus a "financial-health score is
N out of 9" sentence so a beginner sees *cheap and sound*, not jargon.
`build_signal_generator` resolves the map automatically, so `analyse --generator
signals` / `run-daily` pick it up once `ingest-fundamental-periods` has run. No
migration, no new dependency. **Deferred:** the *margin-of-safety* leg of the gate
(B3) — value will require cheap **and** quality-pass **and** a margin of safety
once an intrinsic-value estimate lands.

## Slice 36 — Margin-of-safety gate on the value thesis

[Backlog](backlog.md) item **B3** — the third and final leg of the value gate, so
a `value` thesis now requires cheap **and** financially sound **and** trading at a
discount to a transparent worth estimate. `traders.valuation` capitalizes
**normalized owner earnings** (operating cash flow + capex, averaged over the
recent annual periods of the slice-33 series) at a conservative Gordon multiple
`(1+g)/(r−g)` with `r = 10%`, `g = 2%`, yielding an `IntrinsicValue` — per-share
value, **margin of safety** vs the price, a **buy-below** price, and a reverse-DCF
**implied growth** ("what the price already pays for"). Assumption-explicit by
design (the review asked for transparency, not a black box).

The `SignalThesisGenerator` gains an optional `valuation` map
(`valuation.valuations_asof`, look-ahead-safe: each name's price is the last close
before the decision day, its statements the periods public by then). `_value_thesis`
adds the margin-of-safety leg ahead of the quality leg:

- a cheap, sound name with **margin of safety below the minimum** (default 20%) is
  **vetoed** — no discount to intrinsic value, no thesis;
- when an estimate is present, **margin of safety drives conviction** (≥50% → 5,
  ≥35% → 4, else 3) — replacing the flag-count base, per the review's "conviction
  should mean something"; the quality leg can still nudge it up by one;
- an **unknown** estimate (negative/too-sparse owner earnings, no price/shares)
  **falls back** to the slice-35 behaviour.

**Additive by construction** like the quality leg: no `valuation` map ⇒ byte-identical
to slice 35. The estimate is recorded in the rationale (`Intrinsic value ~$X/sh;
margin of safety Y% (Nyr owner earnings; r 10%, g 2%); price implies Z% growth`),
and `web.explain` gains glossary entries (intrinsic value, margin of safety, owner
earnings) plus a plain "we peg its rough worth near $X a share — about Y% above
today's price" sentence. `build_signal_generator` wires it automatically. No
migration, no new dependency. **Deferred:** a true multi-stage DCF and a
maintenance-vs-growth capex split — the v1 capitalization is intentionally simple
and conservative.

## Slice 37 — Buy-list (names to own at your price)

[Backlog](backlog.md) item **B5** — the review's highest-leverage *behavioural*
change: flip the workflow from reacting to a fresh daily candidate list (which
nudges overtrading) to naming the businesses you'd own and the price you'd pay,
then waiting for the market to come to you. `traders.buylist` + migration
`009_buy_list.sql` add a user-curated `buy_list` table (one row per ticker — a
target buy-below price and an optional note); **user data, never written by the
agents**. `set_target` upserts (preserving `created_at`); `remove_target` /
`load_targets` / `get_target` round it out. `evaluate` joins each target against
the latest close — `triggered` when the price is at/under the target, plus the
distance still to go — and, when a slice-36 valuation map is supplied, the model's
own **suggested buy-below** as a sanity check against a self-chosen target.
`traders buylist {set,remove,status}`. This slice is the data + CLI foundation;
the `/buy-list` web page (the surface the beginner actually lives in) is the next
slice. No migration beyond 009, no new dependency; hermetic tests.

## Slice 38 — Buy-list web page

[Backlog](backlog.md) item **B5** completed: the `/buy-list` surface the beginner
actually lives in, over the slice-37 data layer. A read view lists each target vs
the latest close with a plain-English status — **Ready — at/under your price**
(green) when the price has come to you, or **+X% to go** otherwise — plus the
model's suggested buy-below (slice-36 intrinsic value) as a sanity check, and the
target's note. CSRF-guarded forms add/update a target and remove one, routing
through `traders.buylist` (its own user-data write path, like the slice-32 jobs
toggle — never the agent tables). A "How to read this page" explainer frames it as
*wait for your price*, not an order. New nav entry; reuses the slice-11/13 web
layer + `web` extra. No migration, no new dependency; web tests `importorskip`
FastAPI so the default suite stays hermetic.

## Slice 39 — Honest caveats at the point of claim

[Backlog](backlog.md) item **B8**: the code's honesty (snapshot / US-EOD caveats
that lived only in docstrings and `OPERATING.md`) reaches the screen where the
claim is actually made — the review's praise was for the honesty, so don't hide it
from the user. `ThesisExplanation` gains a `caveat`: the value thesis's "cheap"
ratios (E/P, B/P, FCF yield) come from a single current fundamentals snapshot, not
audited period-by-period history, so the Today and thesis-detail cards now say so
inline ("…a starting point, so check the company's filings before acting"). A
shared `explain.PRICE_CAVEAT` (a Jinja global) notes that displayed prices are
end-of-day and US-only on the free tier — shown on Positions (when prices are on)
and the Buy-list. Subtle `.caveat` styling (muted, info-toned ⓘ — distinct from the
red ⚠ earnings warning) keeps it honest without alarming. Pure-function + template
change; no migration, no new dependency.

## Slice 40 — Reframe the Today page (dampen the daily-action reflex)

[Backlog](backlog.md) item **B9** — the Today page nudged a beginner toward daily
action ("PM picks", "Full breakdown & how to record a buy →"), manufacturing the
overtrading the review warned against. It now leads with a "How to use this page"
box — *these are ideas to research, not orders; most days the right move is to do
nothing* — and points at the Buy-list to **wait for your price**. "PM picks"
becomes "Ideas to research"; the run line reads "N idea(s) worth a look, M set
aside"; the per-card CTA softens to "See the full breakdown →" (the "record a buy"
nudge is gone); "Rejected" becomes "Set aside" (the discipline of *not* acting,
made visible); and the Candidates table gains a "background, not recommendations"
intro. Template/copy only — no data, route, or schema change; pairs with the
slice-37/38 buy-list it now points to.

## Slice 41 — Quality metrics: ROIC / ROE / margin trend

[Backlog](backlog.md) item **B2 (remainder)** — the magnitudes a long-term owner
weighs, complementing slice-34's binary Piotroski tests. `quality.QualityMetrics`
+ `quality_metrics(cur, prev)` / `quality_metrics_for(conn, ticker, as_of=...)`
compute return on invested capital (net income / (equity + long-term debt) — a
statement-only proxy, not tax-adjusted NOPAT), return on equity, and gross margin,
each with its year-over-year delta, look-ahead-safe over the slice-33 series.
Return ratios drop to `None` on a non-positive base (e.g. negative equity) rather
than report a misleading number. Pure stdlib, no migration; compute + accessor now
(surfacing/wiring is a follow-on, as Piotroski was in slice 34). **Deferred:**
interest coverage and debt-maturity need an `interest_expense` line the slice-33
schema doesn't carry yet.

## Future

- More data sources behind the same patterns — fundamentals/news beyond yfinance/EDGAR, or additional price providers (FMP, Polygon). The price ingestor is now multi-source (Stooq, Tiingo); adding another is a `price_source` entry.
- HTML rendering on top of the slice 7 markdown surface, if/when wanted.
- Cross-run data caching if rate limits start mattering.
