# traders

Agent-driven stock research and advisory system. Daily cadence over an S&P 100 + EuroStoxx 50 watchlist, paper mode only — the system never places orders; the user executes manually and reports fills back through a feedback path. Built as plain Python modules sharing a SQLite database.

## Status

- **Scout** — shipped; date-seeded rotation by default, opt-in price-signal ranking (slice 21).
- **Researcher** — shipped; stub data source by default, opt-in yfinance/EDGAR adapters.
- **Analyst** — shipped; stub thesis generator by default, opt-in signal-driven (slice 20) or LLM-backed (Claude, slice 27) generators, all behind a stable protocol.
- **Portfolio Manager** — shipped; persists accept/reject decisions and renders a daily report.
- **Reviewer** (weekly) — shipped; walks closed positions and writes post-mortems (stub by default, opt-in Claude write-up, slice 28).
- **Data sources** — shipped; `stub` default plus opt-in `yfinance` (news/fundamentals) and `edgar` (SEC filings).
- **Signals & prices** — shipped; pure-stdlib signals library (momentum / realized vol / mean-reversion / RSI, slice 18) plus a Stooq price ingestor (slice 19) that fills the `prices` table the ranked Scout and signal generator read.
- **Fundamentals** — shipped; opt-in yfinance fundamentals ingestor (slice 25) fills a point-in-time `fundamentals` table that the slice-26 value signals (E/P, B/P, FCF/P) and earnings-proximity annotation build on, producing `value` theses from the signal generator.
- **Web UI** — shipped; local FastAPI + Jinja2 read views plus feedback actions (slices 11–13).
- **Strategy goal, metrics, parameters, optimizer** — shipped; scores realized results against a numeric goal and proposes human-gated single-variable changes (slices 14–16).
- **Backtest harness** — shipped; replays a parameter set — rotation+stub or the real signal strategy — over historical prices with transaction costs, next-bar fills, and an in-sample/out-of-sample split (slices 17, 22–23).
- **Optimizer OOS gate** — shipped; `optimize --apply` is gated on out-of-sample improvement and Sharpe trial-deflation so the optimizer can't fish (slice 24).
- **LLM generators + eval** — shipped; opt-in Claude-backed thesis (slice 27) and post-mortem (slice 28) generators behind the `llm` extra, plus an eval harness (`eval-llm`, slice 29) that scores them against fixtures as a CI gate.

## Agents

- **Scout** — filters the watchlist down to a small daily candidate set. Date-seeded rotation by default; opt-in price-signal ranking (`--rank signals`) once prices are ingested.
- **Researcher** — per-candidate digest of filings, news, fundamentals into `research_notes` with cited sources.
- **Analyst** — turns each note into zero-or-more theses (type, direction, conviction, suggested size, exit condition). Stub generator by default; opt-in `--generator signals` (price/fundamental-driven) or `--generator llm` (Claude, structured-output tool call).
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
uv run traders web         # local web UI (needs the `web` extra)
uv run traders metrics     # score realized results vs the strategy goal
uv run traders optimize    # propose / apply a single-variable change (apply is OOS-gated)
uv run traders backtest    # replay a parameter set over historical prices
```

Each subcommand takes `--db PATH` and (where applicable) a `--*-run-id` flag to target a specific upstream run instead of the latest. `research` and `run-daily` additionally take `--data-source {stub,yfinance,edgar}` (default `stub`); see [Data sources](#data-sources) below. `pm`, `review`, `run-daily`, and `run-weekly` additionally take `--format {text,markdown}` (default `text`) and `--output PATH` to write the rendered document to a file instead of stdout:

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

The Researcher reads evidence through a `DataSource` protocol; the default is `StubDataSource` (deterministic fixture data, no I/O). Two real adapters are opt-in: `YFinanceDataSource` (news + fundamentals, slice 9) and `EdgarDataSource` (recent 10-K/10-Q/8-K filings from SEC EDGAR, slice 10):

```bash
uv sync --extra realdata                                # installs yfinance
uv run traders research --data-source yfinance          # research using real data
uv run traders run-daily --data-source yfinance         # full chain with real data

export TRADERS_EDGAR_UA="Your Name you@example.com"     # SEC requires a contact UA
uv run traders research --data-source edgar             # filings from SEC EDGAR
```

The default install does **not** include yfinance — keep the footprint empty unless you opt in. EDGAR uses only stdlib (`urllib` + `json`) but hard-requires `TRADERS_EDGAR_UA`. The CLI default is `--data-source stub` so existing behavior and the test suite stay hermetic.

Both adapters degrade gracefully: network errors, rate limits, or missing fields produce an empty list of data points rather than raising, so a single flaky ticker can't kill the run. There is no caching layer — once per close is well within both services' tolerances.

## LLM theses

The Analyst and the weekly Reviewer can ask Claude for their write-ups instead of the deterministic generators, behind the `llm` extra:

```bash
uv sync --extra llm
export ANTHROPIC_API_KEY="sk-ant-..."
export TRADERS_LLM_MODEL="claude-sonnet-4-6"             # optional; this is the default
uv run traders analyse --generator llm                  # theses (or: run-daily --generator llm)
uv run traders review --generator llm                   # post-mortems (or: run-weekly --generator llm)
uv run traders eval-llm --min-pass-rate 0.8             # score the generators vs fixtures (CI gate)
```

`LLMThesisGenerator` (theses) and `LLMPostMortemGenerator` (post-mortems) each force a structured-output tool call, so the model always returns a valid result — a thesis or an explicit decline, or an `{outcome, lessons}` write-up — never free text. The thesis note and the thesis's own text are treated as **untrusted**: wrapped as delimited data with a system instruction never to follow their contents, the tool-only response constrains output to the schema, and the result is validated and clamped before it is stored. The post-mortem generator additionally falls back to a deterministic write-up on any model error, so the weekly run can't be broken by a flaky call. The paper-only and human `--apply` gates are unchanged. The default install does not include `anthropic`; the test suite injects a fake client and stays fully offline.

`eval-llm` scores both generators against golden fixture cases (e.g. a clearly cheap name should yield a thesis; a no-edge name should be declined; a win/loss should produce a grounded post-mortem) and prints a per-case pass/fail report. `--min-pass-rate` makes it exit non-zero below a threshold, so it doubles as a CI gate that catches a prompt or model change that quietly degrades quality. The harness itself is pure stdlib, so it is exercised hermetically in the test suite with fake generators.

## Web UI

A local, server-rendered FastAPI + Jinja2 UI lives behind the `web` extra:

```bash
uv sync --extra web
uv run traders web --db data/traders.db            # http://127.0.0.1:8000
uv run traders web --data-source yfinance          # live prices → unrealized P&L
```

- `/` Today (latest PM picks + candidates), `/positions`, `/theses` (filterable) → `/theses/{id}`, `/reviews`.
- The positions and thesis-detail pages report fills/partials/skips/sells; these call the same `traders.feedback` path as the CLI, behind a per-session CSRF token. Read-only everywhere else.
- Binds to `127.0.0.1` (local only). The CLI feedback path keeps working unchanged.

## Backtesting

Live trading closes a few positions a week — too sparse to tune parameters confidently. The backtest harness replays a parameter set over historical prices and scores it against the same goal:

```bash
uv run traders backtest                                   # synthetic prices, trailing 180d
uv run traders backtest --start 2026-01-01 --end 2026-05-31 \
    --batch-size 12 --max-total-size-pct 24 --format markdown
uv run traders backtest --compare-experiment 1            # baseline vs a proposal's one change
uv run traders backtest --source db                       # use the stored `prices` table
```

It composes the same decision logic the live agents use, so results don't diverge from production; it runs in memory and never writes the live tables. `--source synthetic` (default) is deterministic and needs no data; `--source db` reads the `prices` table — populating it from a real provider is on the roadmap. `--compare-experiment ID` needs an experiment to exist first (create one with `traders optimize`). Entries fill at the decision-day close and trades are equal-weighted — see the `traders.backtest` docstring for the full list of v1 simplifications.

The same machinery backs the **apply gate** (slice 24): `traders optimize --apply ID` now replays the proposal in-sample vs out-of-sample and applies it only if the candidate beats the baseline out-of-sample *and* its per-trade Sharpe survives deflation for the number of proposals tried (so the optimizer can't fish across many tries). It defaults to real ingested prices and prints a gate report; `--force` restores the old unconditional apply after you've reviewed why it blocked.

## Current limitations

- **Quality & PEAD signals deferred.** Value (E/P, B/P, FCF/P) and earnings-proximity are wired into the signal thesis generator (slice 26), but a full Piotroski quality score (needs year-over-year statements) and post-earnings drift / SUE (needs consensus estimates) need richer fundamentals than a single yfinance snapshot — both wait on period-by-period statement ingestion.
- **Scout defaults to date rotation.** Price-signal ranking is opt-in (`--rank signals`) and falls back to rotation when no prices are ingested, so the zero-data path still works.
- **No in-process scheduler.** `run-daily` and `run-weekly` chain the agents end-to-end, but timing (post-close daily, weekly review) is left to the operator — wire them to cron, systemd timers, or whatever the host runs.
- **No caching across runs.** Real data comes from yfinance and SEC EDGAR today; FMP, Polygon, and paid news APIs are future slices. No cross-run cache yet — if rate limits start to bite, that's the trigger to add one.

## Running

```bash
uv sync
uv run pytest
```

## Layout

- `src/traders/` — package source (one module per agent plus `db`, `cli`, `signals`, `data_sources`, `post_mortems`, `feedback`, `reports`, `orchestrator`, `strategy`, `metrics`, `parameters`, `optimizer`, `backtest`, `prices`)
- `src/traders/web/` — FastAPI web UI (`app`, `queries`, `prices`, `csrf`, `templates/`)
- `tests/` — pytest suite
- `migrations/` — SQLite schema migrations, applied in order; append-only
- `data/` — local SQLite db (gitignored)
- `docs/` — slice plan, architecture, glossary

See `docs/slices.md` for the build order and `docs/architecture.md` for the agent shape.
