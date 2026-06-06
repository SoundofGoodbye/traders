"""Ops commands: jobs, buylist, feedback, web."""

from __future__ import annotations

import argparse
from pathlib import Path

from traders.db import apply_migrations, connect
from traders.feedback import (
    FeedbackError,
    record_fill,
    record_partial,
    record_sell,
    record_skip,
)

from traders.cli_commands._common import Command


def _add_jobs(sub: argparse._SubParsersAction) -> None:
    jobs_p = sub.add_parser("jobs", help="Show or toggle the scheduled (cron) jobs")
    jobs_p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory holding jobs.json / cron.log (default: data)",
    )
    jobs_sub = jobs_p.add_subparsers(dest="jobs_action", required=True)
    jobs_sub.add_parser("status", help="Show each job's schedule, on/off state, and last run")
    jobs_enable = jobs_sub.add_parser("enable", help="Enable a job")
    jobs_enable.add_argument("name")
    jobs_disable = jobs_sub.add_parser("disable", help="Disable a job")
    jobs_disable.add_argument("name")
    jobs_check = jobs_sub.add_parser("check", help="Exit 0 if the job is enabled, 1 if disabled")
    jobs_check.add_argument("name")


def _run_jobs(args: argparse.Namespace) -> None:
    from traders import jobs as jobs_mod

    data_dir = args.data_dir
    if args.jobs_action == "check":
        raise SystemExit(0 if jobs_mod.is_enabled(args.name, data_dir) else 1)
    if args.jobs_action in ("enable", "disable"):
        try:
            jobs_mod.set_enabled(args.name, args.jobs_action == "enable", data_dir)
        except ValueError as e:
            print(f"jobs error: {e}")
            raise SystemExit(1) from e
        print(f"{args.jobs_action}d {args.name}")
        return
    # status
    for s in jobs_mod.job_status(data_dir):
        state = "on " if s.enabled else "off"
        last = f"{s.last_run} [{s.last_status}]" if s.last_run else "never run"
        print(f"  [{state}] {s.name:7} {s.schedule:12}  last: {last}")


def _add_buylist(sub: argparse._SubParsersAction) -> None:
    buylist_p = sub.add_parser("buylist", help="Manage your buy-list (names to own at your price)")
    buylist_p.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    buylist_sub = buylist_p.add_subparsers(dest="buylist_action", required=True)
    bl_set = buylist_sub.add_parser("set", help="Add or update a target buy-below price")
    bl_set.add_argument("--ticker", required=True)
    bl_set.add_argument("--target", type=float, required=True, help="Buy at/under this price")
    bl_set.add_argument("--note", default=None, help="Optional reminder of why")
    bl_remove = buylist_sub.add_parser("remove", help="Remove a ticker from the buy-list")
    bl_remove.add_argument("--ticker", required=True)
    buylist_sub.add_parser("status", help="Show targets vs the latest price (which are triggered)")


def _run_buylist(args: argparse.Namespace) -> None:
    from traders import buylist

    conn = connect(args.db)
    apply_migrations(conn)
    if args.buylist_action == "set":
        try:
            t = buylist.set_target(conn, args.ticker, args.target, note=args.note)
        except ValueError as e:
            conn.close()
            raise SystemExit(f"buylist error: {e}") from e
        print(f"set {t.ticker}: buy at/under {t.target_price:.2f}")
        conn.close()
        return
    if args.buylist_action == "remove":
        removed = buylist.remove_target(conn, args.ticker)
        print(f"removed {args.ticker}" if removed else f"{args.ticker} was not on the buy-list")
        conn.close()
        return
    # status — targets vs the latest price, plus the model's suggested buy-below
    from datetime import date

    from traders.prices import load_history_from_db
    from traders.valuation import valuations_asof

    history = load_history_from_db(conn)
    valuation = valuations_asof(conn, history, as_of=date.today())
    rows = buylist.evaluate(conn, history=history, valuation=valuation)
    if not rows:
        print("buy-list is empty — add one: traders buylist set --ticker AAPL --target 150")
    for r in rows:
        price = f"{r.latest_price:.2f}" if r.latest_price is not None else "—"
        if r.triggered:
            flag = "TRIGGERED — at/under your price"
        elif r.distance_pct is not None:
            flag = f"{r.distance_pct:+.1f}% vs target"
        else:
            flag = "no price yet"
        suggested = (
            f"; model buy-below ~{r.suggested_buy_below:.2f}"
            if r.suggested_buy_below is not None
            else ""
        )
        print(
            f"  {r.target.ticker}: target {r.target.target_price:.2f}, "
            f"now {price} [{flag}]{suggested}"
        )
    conn.close()


def _add_web(sub: argparse._SubParsersAction) -> None:
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
    web.add_argument("--port", type=int, default=8420, help="Bind port (default: 8420)")
    web.add_argument(
        "--data-source",
        dest="data_source",
        choices=("stub", "yfinance", "edgar", "edgar-full"),
        default="stub",
        help="Price source for unrealized P&L (default: stub → no live prices)",
    )


def _run_web(args: argparse.Namespace) -> None:
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


def _add_feedback(sub: argparse._SubParsersAction) -> None:
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


def _run_feedback(args: argparse.Namespace) -> None:
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


COMMANDS: list[Command] = [
    Command("jobs", _add_jobs, _run_jobs),
    Command("buylist", _add_buylist, _run_buylist),
    Command("web", _add_web, _run_web),
    Command("feedback", _add_feedback, _run_feedback),
]
