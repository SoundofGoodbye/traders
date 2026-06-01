"""CLI entry point for the traders package."""

from __future__ import annotations

import argparse
from pathlib import Path

from traders import __version__
from traders.analyst import run as analyst_run
from traders.data_sources import make_data_source
from traders.db import apply_migrations, connect
from traders.feedback import (
    FeedbackError,
    record_fill,
    record_partial,
    record_sell,
    record_skip,
)
from traders.orchestrator import run_daily, run_weekly
from traders.portfolio import run as pm_run
from traders.reports import (
    latest_reviewer_run_id,
    load_review_for_run,
    render_daily_report_markdown,
    render_metrics,
    render_weekly_review_markdown,
)
from traders.research import run as research_run
from traders.reviewer import run as reviewer_run
from traders.scout import run as scout_run


def _emit(text: str, output: Path | None) -> None:
    """Write `text` to `output` (creating its parent dir) or to stdout."""
    if output is None:
        print(text, end="" if text.endswith("\n") else "\n")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    print(f"wrote {output}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="traders")
    parser.add_argument("--version", action="version", version=f"traders v{__version__}")
    sub = parser.add_subparsers(dest="cmd")

    scout = sub.add_parser("scout", help="Run the Scout agent")
    scout.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    scout.add_argument("--watchlist", type=Path, default=None, help="Watchlist JSON path")
    scout.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Candidates per run (default: learned parameter)",
    )

    research = sub.add_parser("research", help="Run the Researcher agent")
    research.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    research.add_argument(
        "--scout-run-id",
        type=int,
        default=None,
        help="Scout run to research (defaults to latest)",
    )
    research.add_argument(
        "--data-source",
        dest="data_source",
        choices=("stub", "yfinance", "edgar"),
        default="stub",
        help="Evidence source for the Researcher (default: stub)",
    )

    analyse = sub.add_parser("analyse", help="Run the Analyst agent")
    analyse.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    analyse.add_argument(
        "--research-run-id",
        type=int,
        default=None,
        help="Research run to analyse (defaults to latest)",
    )

    pm = sub.add_parser("pm", help="Run the Portfolio Manager")
    pm.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    pm.add_argument(
        "--analyst-run-id",
        type=int,
        default=None,
        help="Analyst run to review (defaults to latest)",
    )
    pm.add_argument(
        "--max-total-size-pct",
        type=float,
        default=None,
        help="Total exposure cap (%% of NAV; default: learned parameter)",
    )
    pm.add_argument(
        "--format",
        dest="fmt",
        choices=("text", "markdown"),
        default="text",
        help="Output format (default: text)",
    )
    pm.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the rendered report to this path instead of stdout",
    )

    review = sub.add_parser("review", help="Run the Reviewer (weekly)")
    review.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    review.add_argument(
        "--format",
        dest="fmt",
        choices=("text", "markdown"),
        default="text",
        help="Output format (default: text)",
    )
    review.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the rendered review to this path instead of stdout",
    )

    daily = sub.add_parser(
        "run-daily",
        help="Run Scout → Researcher → Analyst → Portfolio Manager in sequence",
    )
    daily.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    daily.add_argument(
        "--watchlist", type=Path, default=None, help="Watchlist JSON path"
    )
    daily.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Candidates per run (default: learned parameter)",
    )
    daily.add_argument(
        "--max-total-size-pct",
        type=float,
        default=None,
        help="Total exposure cap (%% of NAV; default: learned parameter)",
    )
    daily.add_argument(
        "--data-source",
        dest="data_source",
        choices=("stub", "yfinance", "edgar"),
        default="stub",
        help="Evidence source for the Researcher step (default: stub)",
    )
    daily.add_argument(
        "--format",
        dest="fmt",
        choices=("text", "markdown"),
        default="text",
        help="Output format for the PM report (default: text)",
    )
    daily.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the rendered PM report to this path instead of stdout",
    )

    weekly = sub.add_parser("run-weekly", help="Run the weekly Reviewer")
    weekly.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    weekly.add_argument(
        "--format",
        dest="fmt",
        choices=("text", "markdown"),
        default="text",
        help="Output format for the review (default: text)",
    )
    weekly.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the rendered review to this path instead of stdout",
    )

    params_p = sub.add_parser(
        "params", help="Show the active learned parameters"
    )
    params_p.add_argument("--db", type=Path, default=None, help="SQLite DB path")

    metrics_p = sub.add_parser(
        "metrics", help="Score realized results against the strategy goal"
    )
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

    optimize_p = sub.add_parser(
        "optimize",
        help="Propose (review-only) or apply a single-variable strategy change",
    )
    optimize_p.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    opt_action = optimize_p.add_mutually_exclusive_group()
    opt_action.add_argument(
        "--apply", type=int, default=None, metavar="ID",
        help="Apply a proposed experiment by id",
    )
    opt_action.add_argument(
        "--reject", type=int, default=None, metavar="ID",
        help="Reject a proposed experiment by id",
    )

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
    backtest_p.add_argument(
        "--watchlist", type=Path, default=None, help="Watchlist JSON path"
    )
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
        "--batch-size", type=int, default=None, help="Override Scout batch size"
    )
    backtest_p.add_argument(
        "--max-total-size-pct", type=float, default=None, help="Override PM exposure cap"
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

    web = sub.add_parser(
        "web",
        help="Serve the local web UI",
        epilog=(
            "Set TRADERS_WEB_SECRET to a fixed value to keep feedback-form CSRF "
            "cookies valid across restarts; otherwise a fresh secret is generated "
            "per process and open forms must be reloaded after a restart."
        ),
    )
    web.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    web.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    web.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    web.add_argument(
        "--data-source",
        dest="data_source",
        choices=("stub", "yfinance", "edgar"),
        default="stub",
        help="Price source for unrealized P&L (default: stub → no live prices)",
    )

    feedback = sub.add_parser("feedback", help="Report execution feedback")
    fb_sub = feedback.add_subparsers(dest="action", required=True)

    fb_fill = fb_sub.add_parser("fill", help="Full fill at the suggested size")
    fb_fill.add_argument("--db", type=Path, default=None)
    fb_fill.add_argument("--thesis-id", type=int, required=True)
    fb_fill.add_argument("--price", type=float, required=True)
    fb_fill.add_argument("--size-pct", type=float, default=None)
    fb_fill.add_argument("--notes", type=str, default=None)

    fb_partial = fb_sub.add_parser("partial", help="Partial fill at a smaller size")
    fb_partial.add_argument("--db", type=Path, default=None)
    fb_partial.add_argument("--thesis-id", type=int, required=True)
    fb_partial.add_argument("--price", type=float, required=True)
    fb_partial.add_argument("--size-pct", type=float, required=True)
    fb_partial.add_argument("--notes", type=str, default=None)

    fb_skip = fb_sub.add_parser("skip", help="Declined suggested thesis (log only)")
    fb_skip.add_argument("--db", type=Path, default=None)
    fb_skip.add_argument("--thesis-id", type=int, required=True)
    fb_skip.add_argument("--notes", type=str, default=None)

    fb_sell = fb_sub.add_parser("sell", help="Close an open position")
    fb_sell.add_argument("--db", type=Path, default=None)
    fb_sell.add_argument("--price", type=float, required=True)
    fb_sell_target = fb_sell.add_mutually_exclusive_group(required=True)
    fb_sell_target.add_argument("--position-id", type=int, default=None)
    fb_sell_target.add_argument("--thesis-id", type=int, default=None)
    fb_sell.add_argument("--notes", type=str, default=None)

    args = parser.parse_args(argv)

    if args.cmd == "scout":
        conn = connect(args.db)
        apply_migrations(conn)
        run_id, picks = scout_run(conn, watchlist_path=args.watchlist, batch_size=args.batch_size)
        print(f"scout run {run_id}: {len(picks)} candidate(s)")
        for t in picks:
            print(f"  {t}")
        conn.close()
        return

    if args.cmd == "research":
        conn = connect(args.db)
        apply_migrations(conn)
        ds = make_data_source(args.data_source)
        run_id, tickers = research_run(
            conn, data_source=ds, scout_run_id=args.scout_run_id
        )
        print(
            f"research run {run_id}: {len(tickers)} note(s) "
            f"(source: {args.data_source})"
        )
        for t in tickers:
            print(f"  {t}")
        conn.close()
        return

    if args.cmd == "analyse":
        conn = connect(args.db)
        apply_migrations(conn)
        run_id, n_theses = analyst_run(conn, research_run_id=args.research_run_id)
        print(f"analyst run {run_id}: {n_theses} thesis(es)")
        conn.close()
        return

    if args.cmd == "pm":
        conn = connect(args.db)
        apply_migrations(conn)
        report = pm_run(
            conn,
            analyst_run_id=args.analyst_run_id,
            max_total_size_pct=args.max_total_size_pct,
        )
        if args.fmt == "markdown":
            _emit(render_daily_report_markdown(report), args.output)
        else:
            print(
                f"pm run {report.pm_run_id}: "
                f"{len(report.accepted)} accepted, {len(report.rejected)} rejected"
            )
            for item in report.accepted:
                print(
                    f"  accepted: {item.ticker} ({item.thesis_type}, "
                    f"conv {item.conviction}, size {item.suggested_size_pct:.1f}%)"
                )
            for item in report.rejected:
                print(f"  rejected: {item.ticker} — {item.reason}")
        conn.close()
        return

    if args.cmd == "review":
        conn = connect(args.db)
        apply_migrations(conn)
        run_id, n = reviewer_run(conn)
        if args.fmt == "markdown":
            target = run_id if run_id else latest_reviewer_run_id(conn)
            items = load_review_for_run(conn, target) if target else []
            _emit(render_weekly_review_markdown(items, target), args.output)
        else:
            print(f"reviewer run {run_id}: {n} post-mortem(s)")
        conn.close()
        return

    if args.cmd == "run-daily":
        conn = connect(args.db)
        apply_migrations(conn)
        ds = make_data_source(args.data_source)
        result = run_daily(
            conn,
            watchlist_path=args.watchlist,
            batch_size=args.batch_size,
            max_total_size_pct=args.max_total_size_pct,
            data_source=ds,
        )
        # Markdown to stdout: keep it pipeable by suppressing step
        # summaries. In every other case (text, or markdown→file)
        # surface the per-step progress.
        quiet = args.fmt == "markdown" and args.output is None
        if not quiet:
            print(
                f"scout run {result.scout_run_id}: "
                f"{len(result.scout_picks)} candidate(s)"
            )
            print(
                f"research run {result.research_run_id}: "
                f"{len(result.research_tickers)} note(s) "
                f"(source: {args.data_source})"
            )
            print(
                f"analyst run {result.analyst_run_id}: "
                f"{result.analyst_thesis_count} thesis(es)"
            )
        report = result.report
        if args.fmt == "markdown":
            _emit(render_daily_report_markdown(report), args.output)
        else:
            print(
                f"pm run {report.pm_run_id}: "
                f"{len(report.accepted)} accepted, {len(report.rejected)} rejected"
            )
            for item in report.accepted:
                print(
                    f"  accepted: {item.ticker} ({item.thesis_type}, "
                    f"conv {item.conviction}, size {item.suggested_size_pct:.1f}%)"
                )
            for item in report.rejected:
                print(f"  rejected: {item.ticker} — {item.reason}")
        conn.close()
        return

    if args.cmd == "run-weekly":
        conn = connect(args.db)
        apply_migrations(conn)
        result = run_weekly(conn)
        if args.fmt == "markdown":
            target = (
                result.reviewer_run_id
                if result.reviewer_run_id
                else latest_reviewer_run_id(conn)
            )
            items = load_review_for_run(conn, target) if target else []
            _emit(render_weekly_review_markdown(items, target), args.output)
        else:
            print(
                f"reviewer run {result.reviewer_run_id}: "
                f"{result.post_mortems_written} post-mortem(s)"
            )
        conn.close()
        return

    if args.cmd == "params":
        from dataclasses import asdict

        from traders.parameters import load_parameters

        print("active learned parameters:")
        for name, value in asdict(load_parameters()).items():
            print(f"  {name}: {value}")
        return

    if args.cmd == "metrics":
        from traders.metrics import compute_and_score
        from traders.strategy import load_strategy

        conn = connect(args.db)
        apply_migrations(conn)
        card = compute_and_score(conn, load_strategy())
        _emit(render_metrics(card, fmt=args.fmt), args.output)
        conn.close()
        return

    if args.cmd == "optimize":
        from traders.optimizer import (
            OptimizerError,
            apply_experiment,
            list_experiments,
            propose_experiment,
            reject_experiment,
        )

        conn = connect(args.db)
        apply_migrations(conn)
        try:
            if args.apply is not None:
                exp = apply_experiment(conn, args.apply)
                print(f"applied experiment {exp.id}: {exp.param} -> {exp.new_value}")
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
                    print(
                        f"  review-only — apply with: traders optimize --apply {exp.id}"
                    )
            for e in list_experiments(conn):
                print(
                    f"  [{e.status}] #{e.id} {e.param}: "
                    f"{e.old_value} -> {e.new_value}"
                )
        except OptimizerError as e:
            print(f"optimize error: {e}")
            conn.close()
            raise SystemExit(1) from e
        conn.close()
        return

    if args.cmd == "backtest":
        from dataclasses import replace as _replace
        from datetime import date as _date
        from datetime import timedelta as _timedelta

        from traders.backtest import (
            DEFAULT_HOLDING_DAYS,
            DEFAULT_REBALANCE_DAYS,
            BacktestError,
            backtest_experiment,
            run_backtest,
        )
        from traders.parameters import load_parameters
        from traders.prices import load_history_from_db, synthetic_history
        from traders.reports import render_backtest, render_backtest_comparison
        from traders.scout import load_watchlist
        from traders.strategy import load_strategy

        end = _date.fromisoformat(args.end) if args.end else _date.today()
        start = (
            _date.fromisoformat(args.start)
            if args.start
            else end - _timedelta(days=180)
        )
        holding = (
            args.holding_days if args.holding_days is not None else DEFAULT_HOLDING_DAYS
        )
        rebal = (
            args.rebalance_days
            if args.rebalance_days is not None
            else DEFAULT_REBALANCE_DAYS
        )
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
            history = load_history_from_db(
                conn, tickers=watchlist, start=start, end=end
            )
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
            else:
                result = run_backtest(history, params=params, **common)
                _emit(render_backtest(result, fmt=args.fmt), args.output)
        except BacktestError as e:
            print(f"backtest error: {e}")
            conn.close()
            raise SystemExit(1) from e
        conn.close()
        return

    if args.cmd == "web":
        try:
            import uvicorn
        except ImportError as e:
            raise SystemExit(
                "the web UI requires the 'web' extra. Install with: uv sync --extra web"
            ) from e
        from traders.web.app import create_app
        from traders.web.prices import price_fn_for_source

        app = create_app(args.db, price_fn=price_fn_for_source(args.data_source))
        print(f"serving traders web UI on http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
        return

    if args.cmd == "feedback":
        conn = connect(args.db)
        apply_migrations(conn)
        try:
            if args.action == "fill":
                event = record_fill(
                    conn,
                    thesis_id=args.thesis_id,
                    price=args.price,
                    size_pct=args.size_pct,
                    notes=args.notes,
                )
                print(
                    f"fill recorded (feedback {event.feedback_id}): "
                    f"opened position {event.position_id} "
                    f"@ {event.price} size {event.size_pct:.1f}%"
                )
            elif args.action == "partial":
                event = record_partial(
                    conn,
                    thesis_id=args.thesis_id,
                    price=args.price,
                    size_pct=args.size_pct,
                    notes=args.notes,
                )
                print(
                    f"partial recorded (feedback {event.feedback_id}): "
                    f"opened position {event.position_id} "
                    f"@ {event.price} size {event.size_pct:.1f}%"
                )
            elif args.action == "skip":
                event = record_skip(
                    conn,
                    thesis_id=args.thesis_id,
                    notes=args.notes,
                )
                print(f"skip recorded (feedback {event.feedback_id}): thesis {event.thesis_id}")
            elif args.action == "sell":
                event = record_sell(
                    conn,
                    price=args.price,
                    position_id=args.position_id,
                    thesis_id=args.thesis_id,
                    notes=args.notes,
                )
                print(
                    f"sell recorded (feedback {event.feedback_id}): "
                    f"closed position {event.position_id} @ {event.price}"
                )
        except FeedbackError as e:
            print(f"feedback error: {e}")
            conn.close()
            raise SystemExit(1) from e
        conn.close()
        return

    print(f"traders v{__version__}")


if __name__ == "__main__":
    main()
