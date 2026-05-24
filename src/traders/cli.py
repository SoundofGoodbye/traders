"""CLI entry point for the traders package."""

from __future__ import annotations

import argparse
from pathlib import Path

from traders import __version__
from traders.db import apply_migrations, connect
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

    print(f"traders v{__version__}")


if __name__ == "__main__":
    main()
