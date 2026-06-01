# Slice 17 — Backtest harness (plan + premortem)

## Why
Live paper-trading closes a handful of positions per week — far too sparse to
converge the slice-16 Optimizer one variable at a time. A harness that replays a
parameter set over historical prices evaluates a change against months of data in
seconds. This is the piece `docs/slices.md` flagged as the thing that "makes
slice 16 meaningful."

## Design (build on the *pure* decision primitives, not the DB-writing agents)
The live agents already expose their decision logic as pure functions:

- `scout.filter_candidates(watchlist, run_date, batch_size)` → picks
- `signals.StubThesisGenerator().generate(ticker, content)` → draft theses (the `ThesisGenerator` protocol)
- `portfolio.evaluate(theses, open_positions, max_total_size_pct)` → (accepted, rejected)
- `post_mortems.compute_pnl_pct(direction, entry, exit)` → pnl %
- `metrics.score(metrics, goal)` → scorecard

The harness composes exactly these, so it mirrors live behaviour with **no writes
to the live DB** and full determinism. No replay of the DB-mutating `run()`s.

### New pieces
1. `migrations/006_prices.sql` — `prices(ticker, day, close)` historical store.
2. `traders/prices.py` — `PriceHistory` (as-of close lookup), `synthetic_history`
   (deterministic walk, hashlib-seeded — for tests/demos with zero setup),
   `load_history_from_db` / `save_prices` (the real store). Zero-dep, core.
3. `traders/backtest.py` — `run_backtest(history, *, params, goal, start, end,
   holding_days, rebalance_every_days, watchlist, generator)` → `BacktestResult`
   (metrics + scorecard + trades). Plus `compare_params` and `backtest_experiment`
   (the optimizer tie-in).
4. `metrics.py` — extract pure `metrics_from_trades(trades, now)`; `compute_metrics`
   delegates to it. Lets the harness reuse the exact metric math (fidelity).
5. `reports.render_backtest` / `render_backtest_comparison` (text + markdown).
6. CLI `traders backtest` (`--source synthetic|db`, window, holding/rebalance,
   param overrides, `--compare-experiment ID`, `--format`, `--output`).

### Simulation model
Iterate rebalance dates from `start` to `end` stepping `rebalance_every_days`. At
each date: (1) close positions whose `exit_day <= today` at the as-of close;
(2) run filter→generate→evaluate against currently-open sim positions to get
accepted picks; (3) open each accepted pick at today's as-of close, scheduling
`exit_day = today + holding_days`. After the loop, realize all still-open
positions. Score the realized trades with the existing metrics + goal.

Sizing matches live metrics: PnL is price-only and equally weighted per trade;
position size only affects *which* trades the PM accepts (exposure cap), never an
individual trade's pnl%. This keeps the backtest scorecard identical in spirit to
the live one.

## Premortem — how this could go wrong, and the mitigation
- **Non-determinism.** `hash()` is per-process salted → use `hashlib.sha256` for
  synthetic prices. Dict iteration is insertion-ordered; sort trades by
  `(exit_day, entry_day, ticker)` before metrics so drawdown is stable.
- **Drawdown depends on trade order.** `metrics_from_trades` does NOT sort (the DB
  path sorts in SQL); the harness must pass trades oldest-close-first. Documented + enforced.
- **Boundary fuzz in `return_pct_30d`.** Date-string vs datetime-string compare is
  off-by-up-to-a-day at the 30-day cutoff. Acceptable; noted. Tests assert on
  total_pnl / counts / drawdown / determinism, not knife-edge 30d windows.
- **Missing prices.** A ticker with no close on/before a date can't be entered or
  exited; count `skipped_no_price`, exclude `pnl is None` trades from metrics. No crash.
- **Scope creep.** Real price *ingestion* (yfinance → prices table) is deliberately
  deferred to the improvement plan (it's the data-collection theme). The harness
  ships with synthetic (hermetic) + db sources; the db source is round-trip tested.
- **Zero-dep / hermetic.** Everything is stdlib (hashlib, sqlite3, datetime). Web
  and realdata extras are untouched. Default `uv run pytest` stays offline.
- **Fidelity drift.** By composing `filter_candidates` / `evaluate` / `score`
  directly (not re-implementing them), the harness can't silently diverge from live.
