"""CLI entry point for the traders package."""

from __future__ import annotations

import argparse
from pathlib import Path

from traders import __version__
from traders.analyst import run as analyst_run
from traders.db import apply_migrations, connect
from traders.portfolio import run as pm_run
from traders.research import run as research_run
from traders.scout import run as scout_run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="traders")
    parser.add_argument(
        "--version", action="version", version=f"traders v{__version__}"
    )
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
        help="Total exposure cap (% of NAV)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "scout":
        conn = connect(args.db)
        apply_migrations(conn)
        run_id, picks = scout_run(
            conn, watchlist_path=args.watchlist, batch_size=args.batch_size
        )
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

    print(f"traders v{__version__}")


if __name__ == "__main__":
    main()
