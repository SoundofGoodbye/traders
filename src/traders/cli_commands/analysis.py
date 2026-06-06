"""Analysis commands: params, metrics, optimize, backtest, exposure, capital-allocation."""

from __future__ import annotations

import argparse
from pathlib import Path

from traders.db import apply_migrations, connect
from traders.reports import render_metrics

from traders.cli_commands._common import Command, _apply_with_gate, _emit


def _add_params(sub: argparse._SubParsersAction) -> None:
    params_p = sub.add_parser("params", help="Show the active learned parameters")
    params_p.add_argument("--db", type=Path, default=None, help="SQLite DB path")


def _run_params(args: argparse.Namespace) -> None:
    from dataclasses import asdict

    from traders.parameters import load_parameters

    print("active learned parameters:")
    for name, value in asdict(load_parameters()).items():
        print(f"  {name}: {value}")


def _add_capital_allocation(sub: argparse._SubParsersAction) -> None:
    capalloc_p = sub.add_parser(
        "capital-allocation",
        help="A company's capital-allocation record from SEC EDGAR (needs TRADERS_EDGAR_UA)",
    )
    capalloc_p.add_argument("--ticker", required=True, help="Ticker to look up")


def _run_capital_allocation(args: argparse.Namespace) -> None:
    from traders.capital_allocation import capital_allocation_from_facts
    from traders.edgar_fundamentals import companyfacts_fetcher

    try:
        fetch = companyfacts_fetcher()
    except RuntimeError as e:
        raise SystemExit(str(e)) from e
    ca = capital_allocation_from_facts(fetch(args.ticker))
    if ca is None:
        print(f"No capital-allocation data for {args.ticker}.")
        return
    print(f"{args.ticker} — capital allocation over {ca.years} year(s):")
    print(
        f"  buybacks {ca.total_buybacks:,.0f} · dividends {ca.total_dividends:,.0f} · "
        f"returned {ca.total_returned:,.0f}"
    )
    if ca.payout_ratio is not None:
        print(f"  payout ratio (returned / net income): {ca.payout_ratio * 100:.0f}%")
    if ca.share_change_pct is not None:
        print(f"  share count change over the window: {ca.share_change_pct:+.0f}%")
    for note in ca.notes:
        print(f"  • {note}")


def _add_exposure(sub: argparse._SubParsersAction) -> None:
    exposure_p = sub.add_parser(
        "exposure", help="Concentration + hidden correlation across your open positions"
    )
    exposure_p.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    exposure_p.add_argument(
        "--min-corr",
        type=float,
        default=0.8,
        help="Flag position pairs correlated at/above this (default: 0.8)",
    )


def _run_exposure(args: argparse.Namespace) -> None:
    from traders.exposure import exposure_report

    conn = connect(args.db)
    apply_migrations(conn)
    report = exposure_report(conn, min_corr=args.min_corr)
    conn.close()
    c = report.concentration
    if c.num_positions == 0:
        print("No open positions.")
        return
    print(f"Open positions: {c.num_positions} — total size {c.total_size_pct:.1f}%")
    print(
        f"  largest: {c.largest_ticker} {c.largest_pct:.1f}% · "
        f"top 3: {c.top3_pct:.1f}% · concentration index {c.herfindahl:.2f}"
    )
    if report.correlated_pairs:
        print("  highly-correlated pairs (effectively the same bet):")
        for p in report.correlated_pairs:
            print(f"    {p.a} ~ {p.b}: {p.correlation:+.2f}")
    else:
        print("  no highly-correlated pairs among open positions.")


def _add_metrics(sub: argparse._SubParsersAction) -> None:
    metrics_p = sub.add_parser("metrics", help="Score realized results against the strategy goal")
    metrics_p.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    metrics_p.add_argument(
        "--format",
        dest="fmt",
        choices=("text", "markdown"),
        default="text",
        help="Output format (default: text)",
    )
    metrics_p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the rendered scorecard to this path instead of stdout",
    )


def _run_metrics(args: argparse.Namespace) -> None:
    from traders.metrics import compute_and_score
    from traders.strategy import load_strategy

    conn = connect(args.db)
    apply_migrations(conn)
    card = compute_and_score(conn, load_strategy())
    _emit(render_metrics(card, fmt=args.fmt), args.output)
    conn.close()


def _add_optimize(sub: argparse._SubParsersAction) -> None:
    optimize_p = sub.add_parser(
        "optimize",
        help="Propose (review-only) or apply a single-variable strategy change",
    )
    optimize_p.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    opt_action = optimize_p.add_mutually_exclusive_group()
    opt_action.add_argument(
        "--apply",
        type=int,
        default=None,
        metavar="ID",
        help="Apply a proposed experiment by id",
    )
    opt_action.add_argument(
        "--reject",
        type=int,
        default=None,
        metavar="ID",
        help="Reject a proposed experiment by id",
    )
    # --apply is gated on an out-of-sample backtest (slice 24); these tune it.
    optimize_p.add_argument(
        "--force",
        action="store_true",
        help="Apply without the out-of-sample gate. Use only after reviewing why the gate blocked.",
    )
    optimize_p.add_argument(
        "--source",
        choices=("db", "synthetic"),
        default="db",
        help="Price source for the --apply gate (default: db — real ingested "
        "prices; 'synthetic' is illustrative only and warns).",
    )
    optimize_p.add_argument(
        "--strategy",
        choices=("rotation", "signals"),
        default="signals",
        help="Strategy the gate replays (default: signals — the real strategy).",
    )
    optimize_p.add_argument(
        "--watchlist", type=Path, default=None, help="Watchlist JSON path for the gate"
    )
    optimize_p.add_argument(
        "--start",
        type=str,
        default=None,
        help="Gate window start YYYY-MM-DD (default: end-365d)",
    )
    optimize_p.add_argument(
        "--end", type=str, default=None, help="Gate window end YYYY-MM-DD (default: today)"
    )
    optimize_p.add_argument(
        "--holding-days", type=int, default=None, help="Holding period per trade (gate)"
    )
    optimize_p.add_argument(
        "--rebalance-days", type=int, default=None, help="Days between rebalances (gate)"
    )
    optimize_p.add_argument(
        "--oos-fraction",
        type=float,
        default=None,
        help="Out-of-sample tail the gate decides on (default: 0.3)",
    )
    optimize_p.add_argument(
        "--min-dsr",
        type=float,
        default=None,
        help="Min deflated Sharpe (0-1) the candidate must clear (default: 0.95)",
    )
    optimize_p.add_argument(
        "--params",
        type=Path,
        default=None,
        help="Parameters JSON the gate reads as baseline and an apply writes to "
        "(default: the active learned set)",
    )
    optimize_p.add_argument(
        "--format",
        dest="fmt",
        choices=("text", "markdown"),
        default="text",
        help="Gate report format (default: text)",
    )
    optimize_p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the gate report to this path instead of stdout",
    )


def _run_optimize(args: argparse.Namespace) -> None:
    from traders.backtest import BacktestError
    from traders.optimizer import (
        OptimizerError,
        list_experiments,
        propose_experiment,
        reject_experiment,
    )

    conn = connect(args.db)
    apply_migrations(conn)
    try:
        if args.apply is not None:
            _apply_with_gate(conn, args)
        elif args.reject is not None:
            exp = reject_experiment(conn, args.reject)
            print(f"rejected experiment {exp.id}")
        else:
            exp = propose_experiment(conn)
            if exp is None:
                print(
                    "no proposal: strategy is on track or lacks enough "
                    "closed positions to learn from"
                )
            else:
                print(
                    f"proposed experiment {exp.id} ({exp.status}): "
                    f"{exp.param} {exp.old_value} -> {exp.new_value}"
                )
                print(f"  hypothesis: {exp.hypothesis}")
                print(f"  review-only — apply with: traders optimize --apply {exp.id}")
        for e in list_experiments(conn):
            print(f"  [{e.status}] #{e.id} {e.param}: {e.old_value} -> {e.new_value}")
    except (OptimizerError, BacktestError) as e:
        print(f"optimize error: {e}")
        conn.close()
        raise SystemExit(1) from e
    conn.close()


def _add_backtest(sub: argparse._SubParsersAction) -> None:
    backtest_p = sub.add_parser(
        "backtest",
        help="Replay a parameter set over historical prices and score it",
    )
    backtest_p.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    backtest_p.add_argument(
        "--source",
        choices=("synthetic", "db"),
        default="synthetic",
        help="Price history source (default: synthetic — deterministic, no setup)",
    )
    backtest_p.add_argument("--watchlist", type=Path, default=None, help="Watchlist JSON path")
    backtest_p.add_argument(
        "--start", type=str, default=None, help="Window start YYYY-MM-DD (default: end-180d)"
    )
    backtest_p.add_argument(
        "--end", type=str, default=None, help="Window end YYYY-MM-DD (default: today)"
    )
    backtest_p.add_argument(
        "--holding-days", type=int, default=None, help="Holding period per trade"
    )
    backtest_p.add_argument(
        "--rebalance-days", type=int, default=None, help="Days between rebalances"
    )
    backtest_p.add_argument(
        "--cost-bps",
        type=float,
        default=0.0,
        help="Round-trip transaction cost per trade, in basis points (default: 0)",
    )
    backtest_p.add_argument(
        "--entry-lag-days",
        type=int,
        default=0,
        help="Enter this many days after the decision close (removes same-close "
        "optimism; default: 0)",
    )
    backtest_p.add_argument(
        "--oos-fraction",
        type=float,
        default=0.0,
        help="If >0, split the window and report in-sample vs out-of-sample "
        "(overfitting check; e.g. 0.3)",
    )
    backtest_p.add_argument(
        "--batch-size", type=int, default=None, help="Override Scout batch size"
    )
    backtest_p.add_argument(
        "--max-total-size-pct", type=float, default=None, help="Override PM exposure cap"
    )
    backtest_p.add_argument(
        "--strategy",
        choices=("rotation", "signals"),
        default="rotation",
        help="Replay the rotation+stub placeholder or the real 'signals' strategy "
        "(needs enough price history). Default: rotation.",
    )
    backtest_p.add_argument(
        "--params",
        type=Path,
        default=None,
        help="Load parameters from this JSON file instead of the active learned set",
    )
    backtest_p.add_argument(
        "--compare-experiment",
        type=int,
        default=None,
        metavar="ID",
        help="Compare baseline params vs the change proposed in experiment ID",
    )
    backtest_p.add_argument(
        "--format",
        dest="fmt",
        choices=("text", "markdown"),
        default="text",
        help="Output format (default: text)",
    )
    backtest_p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the rendered result to this path instead of stdout",
    )


def _run_backtest(args: argparse.Namespace) -> None:
    from dataclasses import replace as _replace
    from datetime import date as _date
    from datetime import timedelta as _timedelta

    from traders.backtest import (
        DEFAULT_HOLDING_DAYS,
        DEFAULT_REBALANCE_DAYS,
        BacktestError,
        backtest_experiment,
        run_backtest,
        split_backtest,
    )
    from traders.parameters import load_parameters
    from traders.prices import load_history_from_db, synthetic_history
    from traders.reports import render_backtest, render_backtest_comparison
    from traders.scout import load_watchlist
    from traders.strategy import load_strategy

    end = _date.fromisoformat(args.end) if args.end else _date.today()
    start = _date.fromisoformat(args.start) if args.start else end - _timedelta(days=180)
    holding = args.holding_days if args.holding_days is not None else DEFAULT_HOLDING_DAYS
    rebal = args.rebalance_days if args.rebalance_days is not None else DEFAULT_REBALANCE_DAYS
    watchlist = load_watchlist(args.watchlist)
    params = load_parameters(args.params)
    if args.batch_size is not None:
        params = _replace(params, batch_size=args.batch_size)
    if args.max_total_size_pct is not None:
        params = _replace(params, max_total_size_pct=args.max_total_size_pct)
    goal = load_strategy()

    conn = connect(args.db)
    apply_migrations(conn)
    if args.source == "db":
        history = load_history_from_db(conn, tickers=watchlist, start=start, end=end)
        if not history.tickers():
            print(
                "note: the prices table is empty for this window — ingest "
                "historical closes first, or drop --source db to use the "
                "deterministic synthetic source (no data needed)."
            )
    else:
        history = synthetic_history(watchlist, start, end)

    common = dict(
        goal=goal,
        start=start,
        end=end,
        holding_days=holding,
        rebalance_every_days=rebal,
        watchlist=watchlist,
        use_signals=(args.strategy == "signals"),
        cost_bps=args.cost_bps,
        entry_lag_days=args.entry_lag_days,
    )
    try:
        if args.compare_experiment is not None:
            baseline, candidate = backtest_experiment(
                conn, args.compare_experiment, history, params=params, **common
            )
            _emit(
                render_backtest_comparison(baseline, candidate, fmt=args.fmt),
                args.output,
            )
        elif args.oos_fraction > 0:
            in_s, out_s = split_backtest(
                history, params=params, oos_fraction=args.oos_fraction, **common
            )
            heading = "## " if args.fmt == "markdown" else ""
            parts = [
                f"{heading}In-sample",
                render_backtest(in_s, fmt=args.fmt),
                f"{heading}Out-of-sample",
                render_backtest(out_s, fmt=args.fmt),
            ]
            _emit("\n".join(parts) + "\n", args.output)
        else:
            result = run_backtest(history, params=params, **common)
            _emit(render_backtest(result, fmt=args.fmt), args.output)
    except BacktestError as e:
        print(f"backtest error: {e}")
        conn.close()
        raise SystemExit(1) from e
    conn.close()


COMMANDS: list[Command] = [
    Command("params", _add_params, _run_params),
    Command("capital-allocation", _add_capital_allocation, _run_capital_allocation),
    Command("exposure", _add_exposure, _run_exposure),
    Command("metrics", _add_metrics, _run_metrics),
    Command("optimize", _add_optimize, _run_optimize),
    Command("backtest", _add_backtest, _run_backtest),
]
