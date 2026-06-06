"""Pipeline commands: scout, research, analyse, pm, review, run-daily, run-weekly."""

from __future__ import annotations

import argparse
from pathlib import Path

from traders.analyst import run as analyst_run
from traders.data_sources import make_data_source
from traders.db import apply_migrations, connect
from traders.orchestrator import run_daily, run_weekly
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

from traders.cli_commands._common import Command, _emit, _llm_extra_guard


def _add_scout(sub: argparse._SubParsersAction) -> None:
    scout = sub.add_parser("scout", help="Run the Scout agent")
    scout.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    scout.add_argument("--watchlist", type=Path, default=None, help="Watchlist JSON path")
    scout.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Candidates per run (default: learned parameter)",
    )
    scout.add_argument(
        "--rank",
        choices=("rotation", "signals"),
        default="rotation",
        help="Selection: 'rotation' (date cycle) or 'signals' (price-ranked; "
        "needs ingested prices, falls back to rotation). Default: rotation.",
    )


def _run_scout(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    apply_migrations(conn)
    history = None
    if args.rank == "signals":
        from traders.prices import load_history_from_db

        history = load_history_from_db(conn)
    run_id, picks = scout_run(
        conn,
        watchlist_path=args.watchlist,
        batch_size=args.batch_size,
        history=history,
    )
    print(f"scout run {run_id}: {len(picks)} candidate(s)")
    for t in picks:
        print(f"  {t}")
    conn.close()


def _add_research(sub: argparse._SubParsersAction) -> None:
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
        choices=("stub", "yfinance", "edgar", "edgar-full"),
        default="stub",
        help="Evidence source for the Researcher (default: stub)",
    )


def _run_research(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    apply_migrations(conn)
    ds = make_data_source(args.data_source)
    run_id, tickers = research_run(conn, data_source=ds, scout_run_id=args.scout_run_id)
    print(f"research run {run_id}: {len(tickers)} note(s) (source: {args.data_source})")
    for t in tickers:
        print(f"  {t}")
    conn.close()


def _add_analyse(sub: argparse._SubParsersAction) -> None:
    analyse = sub.add_parser("analyse", help="Run the Analyst agent")
    analyse.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    analyse.add_argument(
        "--research-run-id",
        type=int,
        default=None,
        help="Research run to analyse (defaults to latest)",
    )
    analyse.add_argument(
        "--generator",
        choices=("stub", "signals", "llm"),
        default="stub",
        help="Thesis generator: 'stub' (canned), 'signals' (price/fundamental-driven; "
        "needs ingested data), or 'llm' (Claude; needs the 'llm' extra + "
        "ANTHROPIC_API_KEY). Default: stub.",
    )


def _run_analyse(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    apply_migrations(conn)
    generator = None
    if args.generator == "signals":
        from traders.signals_thesis import build_signal_generator

        generator = build_signal_generator(conn)
    elif args.generator == "llm":
        from traders.llm_thesis import LLMThesisGenerator

        generator = LLMThesisGenerator()
    with _llm_extra_guard(args.generator == "llm", conn):
        run_id, n_theses = analyst_run(
            conn, generator=generator, research_run_id=args.research_run_id
        )
    print(f"analyst run {run_id}: {n_theses} thesis(es) (generator: {args.generator})")
    conn.close()


def _add_pm(sub: argparse._SubParsersAction) -> None:
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


def _run_pm(args: argparse.Namespace) -> None:
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


def _add_review(sub: argparse._SubParsersAction) -> None:
    review = sub.add_parser("review", help="Run the Reviewer (weekly)")
    review.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    review.add_argument(
        "--generator",
        choices=("stub", "llm"),
        default="stub",
        help="Post-mortem writer: 'stub' (canned) or 'llm' (Claude; needs the "
        "'llm' extra + ANTHROPIC_API_KEY). Default: stub.",
    )
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


def _run_review(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    apply_migrations(conn)
    reviewer_generator = None
    if args.generator == "llm":
        from traders.llm_postmortem import LLMPostMortemGenerator

        reviewer_generator = LLMPostMortemGenerator()
    with _llm_extra_guard(args.generator == "llm", conn):
        run_id, n = reviewer_run(conn, generator=reviewer_generator)
    if args.fmt == "markdown":
        target = run_id if run_id else latest_reviewer_run_id(conn)
        items = load_review_for_run(conn, target) if target else []
        _emit(render_weekly_review_markdown(items, target), args.output)
    else:
        print(f"reviewer run {run_id}: {n} post-mortem(s) (generator: {args.generator})")
    conn.close()


def _add_run_daily(sub: argparse._SubParsersAction) -> None:
    daily = sub.add_parser(
        "run-daily",
        help="Run Scout → Researcher → Analyst → Portfolio Manager in sequence",
    )
    daily.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    daily.add_argument("--watchlist", type=Path, default=None, help="Watchlist JSON path")
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
        choices=("stub", "yfinance", "edgar", "edgar-full"),
        default="stub",
        help="Evidence source for the Researcher step (default: stub)",
    )
    daily.add_argument(
        "--generator",
        choices=("stub", "signals", "llm"),
        default="stub",
        help="Thesis generator: 'stub', 'signals' (price/fundamental-driven), or "
        "'llm' (Claude; needs the 'llm' extra + ANTHROPIC_API_KEY). Default: stub.",
    )
    daily.add_argument(
        "--rank",
        choices=("rotation", "signals"),
        default="rotation",
        help="Scout selection: 'rotation' or 'signals' (price-ranked). Default: rotation.",
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


def _run_run_daily(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    apply_migrations(conn)
    ds = make_data_source(args.data_source)
    generator = None
    if args.generator == "signals":
        from traders.signals_thesis import build_signal_generator

        generator = build_signal_generator(conn)
    elif args.generator == "llm":
        from traders.llm_thesis import LLMThesisGenerator

        generator = LLMThesisGenerator()
    scout_history = None
    if args.rank == "signals":
        from traders.prices import load_history_from_db

        scout_history = load_history_from_db(conn)
    with _llm_extra_guard(args.generator == "llm", conn):
        result = run_daily(
            conn,
            watchlist_path=args.watchlist,
            batch_size=args.batch_size,
            max_total_size_pct=args.max_total_size_pct,
            data_source=ds,
            analyst_generator=generator,
            scout_history=scout_history,
        )
    # Markdown to stdout: keep it pipeable by suppressing step
    # summaries. In every other case (text, or markdown→file)
    # surface the per-step progress.
    quiet = args.fmt == "markdown" and args.output is None
    if not quiet:
        print(f"scout run {result.scout_run_id}: {len(result.scout_picks)} candidate(s)")
        print(
            f"research run {result.research_run_id}: "
            f"{len(result.research_tickers)} note(s) "
            f"(source: {args.data_source})"
        )
        print(f"analyst run {result.analyst_run_id}: {result.analyst_thesis_count} thesis(es)")
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


def _add_run_weekly(sub: argparse._SubParsersAction) -> None:
    weekly = sub.add_parser("run-weekly", help="Run the weekly Reviewer")
    weekly.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    weekly.add_argument(
        "--generator",
        choices=("stub", "llm"),
        default="stub",
        help="Post-mortem writer: 'stub' or 'llm' (Claude; needs the 'llm' extra "
        "+ ANTHROPIC_API_KEY). Default: stub.",
    )
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


def _run_run_weekly(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    apply_migrations(conn)
    reviewer_generator = None
    if args.generator == "llm":
        from traders.llm_postmortem import LLMPostMortemGenerator

        reviewer_generator = LLMPostMortemGenerator()
    with _llm_extra_guard(args.generator == "llm", conn):
        result = run_weekly(conn, reviewer_generator=reviewer_generator)
    if args.fmt == "markdown":
        target = result.reviewer_run_id if result.reviewer_run_id else latest_reviewer_run_id(conn)
        items = load_review_for_run(conn, target) if target else []
        _emit(render_weekly_review_markdown(items, target), args.output)
    else:
        print(
            f"reviewer run {result.reviewer_run_id}: "
            f"{result.post_mortems_written} post-mortem(s) (generator: {args.generator})"
        )
    conn.close()


COMMANDS: list[Command] = [
    Command("scout", _add_scout, _run_scout),
    Command("research", _add_research, _run_research),
    Command("analyse", _add_analyse, _run_analyse),
    Command("pm", _add_pm, _run_pm),
    Command("review", _add_review, _run_review),
    Command("run-daily", _add_run_daily, _run_run_daily),
    Command("run-weekly", _add_run_weekly, _run_run_weekly),
]
