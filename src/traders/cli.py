"""CLI entry point for the traders package."""

from __future__ import annotations

import argparse
from pathlib import Path

from traders import __version__
from traders.analyst import run as analyst_run
from traders.db import apply_migrations, connect
from traders.feedback import (
    FeedbackError,
    record_fill,
    record_partial,
    record_sell,
    record_skip,
)
from traders.portfolio import run as pm_run
from traders.reports import (
    latest_reviewer_run_id,
    load_review_for_run,
    render_daily_report_markdown,
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
    scout.add_argument("--batch-size", type=int, default=10)

    research = sub.add_parser("research", help="Run the Researcher agent")
    research.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    research.add_argument(
        "--scout-run-id",
        type=int,
        default=None,
        help="Scout run to research (defaults to latest)",
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
        default=20.0,
        help="Total exposure cap (%% of NAV)",
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
        run_id, tickers = research_run(conn, scout_run_id=args.scout_run_id)
        print(f"research run {run_id}: {len(tickers)} note(s)")
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
