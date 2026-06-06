"""Data commands: universe, ingest-prices, ingest-fundamentals, ingest-fundamental-periods, eval-llm."""

from __future__ import annotations

import argparse
from pathlib import Path

from traders.db import apply_migrations, connect

from traders.cli_commands._common import Command, _emit, _llm_extra_guard


def _add_universe(sub: argparse._SubParsersAction) -> None:
    universe_p = sub.add_parser(
        "universe", help="Show which watchlist names are actually priceable (vs skipped)"
    )
    universe_p.add_argument("--watchlist", type=Path, default=None, help="Watchlist JSON path")
    universe_p.add_argument(
        "--source",
        choices=("tiingo", "stooq"),
        default="tiingo",
        help="Price source whose coverage to check (default: tiingo)",
    )


def _run_universe(args: argparse.Namespace) -> None:
    from traders.universe import classify_watchlist

    report = classify_watchlist(args.watchlist, source=args.source)
    print(
        f"Universe ({report.source}): {report.total} tickers — "
        f"{len(report.priceable)} priceable, {len(report.skipped)} skipped "
        f"(foreign venues are not on the free US tier)."
    )
    if report.skipped:
        print(f"  skipped: {', '.join(report.skipped)}")


def _add_ingest_prices(sub: argparse._SubParsersAction) -> None:
    ingest_p = sub.add_parser(
        "ingest-prices",
        help="Fetch daily closes into the prices table "
        "(Tiingo by default — needs TIINGO_API_KEY; or Stooq)",
    )
    ingest_p.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    ingest_p.add_argument("--watchlist", type=Path, default=None, help="Watchlist JSON path")
    ingest_p.add_argument(
        "--source",
        choices=("tiingo", "stooq"),
        default="tiingo",
        help="Price source: 'tiingo' (default; split/div-adjusted EOD, needs "
        "TIINGO_API_KEY) or 'stooq' (free CSV, now apikey-gated upstream).",
    )
    ingest_p.add_argument(
        "--ticker",
        action="append",
        default=None,
        metavar="SYM",
        help="Ticker to ingest (repeatable; default: the whole watchlist)",
    )
    ingest_p.add_argument(
        "--since", type=str, default=None, help="Only keep closes on/after YYYY-MM-DD"
    )
    ingest_p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between requests (be polite to the provider; default: 1.0)",
    )


def _run_ingest_prices(args: argparse.Namespace) -> None:
    from traders.price_ingest import ingest_prices, price_source
    from traders.scout import load_watchlist

    conn = connect(args.db)
    apply_migrations(conn)
    tickers = args.ticker if args.ticker else load_watchlist(args.watchlist)
    try:
        fetch_csv, parse, symbol_map = price_source(args.source, start_date=args.since)
    except (RuntimeError, ValueError) as e:
        conn.close()
        raise SystemExit(str(e)) from e
    result = ingest_prices(
        conn,
        tickers,
        fetch_csv=fetch_csv,
        parse=parse,
        symbol_map=symbol_map,
        since=args.since,
        delay_s=args.delay,
    )
    written = result["written"]
    skipped = result["skipped"]
    total = sum(written.values())
    print(
        f"ingested {total} close(s) for {len(written)} ticker(s); "
        f"{len(skipped)} skipped (source: {args.source})"
    )
    for ticker, n in written.items():
        print(f"  {ticker}: {n}")
    if skipped:
        print(f"  skipped: {', '.join(skipped)}")
    conn.close()


def _add_ingest_fundamentals(sub: argparse._SubParsersAction) -> None:
    ingest_f = sub.add_parser(
        "ingest-fundamentals",
        help="Fetch fundamental snapshots into the fundamentals table "
        "(yfinance; needs the 'realdata' extra)",
    )
    ingest_f.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    ingest_f.add_argument("--watchlist", type=Path, default=None, help="Watchlist JSON path")
    ingest_f.add_argument(
        "--ticker",
        action="append",
        default=None,
        metavar="SYM",
        help="Ticker to ingest (repeatable; default: the whole watchlist)",
    )
    ingest_f.add_argument(
        "--as-of",
        dest="as_of",
        type=str,
        default=None,
        help="Stamp snapshots with this ISO date (default: today)",
    )
    ingest_f.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between requests (be polite to Yahoo; default: 1.0)",
    )


def _run_ingest_fundamentals(args: argparse.Namespace) -> None:
    from traders.fundamentals import ingest_fundamentals
    from traders.scout import load_watchlist

    conn = connect(args.db)
    apply_migrations(conn)
    tickers = args.ticker if args.ticker else load_watchlist(args.watchlist)
    try:
        result = ingest_fundamentals(conn, tickers, as_of=args.as_of, delay_s=args.delay)
    except ImportError as e:
        conn.close()
        raise SystemExit(str(e)) from e
    written = result["written"]
    skipped = result["skipped"]
    print(f"ingested fundamentals for {len(written)} ticker(s); {len(skipped)} skipped")
    for ticker in written:
        print(f"  {ticker}")
    if skipped:
        print(f"  skipped: {', '.join(skipped)}")
    conn.close()


def _add_ingest_fundamental_periods(sub: argparse._SubParsersAction) -> None:
    ingest_fp = sub.add_parser(
        "ingest-fundamental-periods",
        help="Fetch period-by-period statement history into the "
        "fundamental_periods table (yfinance; needs the 'realdata' extra)",
    )
    ingest_fp.add_argument("--db", type=Path, default=None, help="SQLite DB path")
    ingest_fp.add_argument("--watchlist", type=Path, default=None, help="Watchlist JSON path")
    ingest_fp.add_argument(
        "--ticker",
        action="append",
        default=None,
        metavar="SYM",
        help="Ticker to ingest (repeatable; default: the whole watchlist)",
    )
    ingest_fp.add_argument(
        "--source",
        choices=("yfinance", "edgar"),
        default="yfinance",
        help="Statements source: 'yfinance' (default; needs the 'realdata' extra) or "
        "'edgar' (official audited SEC companyfacts; needs TRADERS_EDGAR_UA)",
    )
    ingest_fp.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between requests (be polite to the provider; default: 1.0)",
    )


def _run_ingest_fundamental_periods(args: argparse.Namespace) -> None:
    from traders.fundamental_periods import ingest_fundamental_periods
    from traders.scout import load_watchlist

    conn = connect(args.db)
    apply_migrations(conn)
    tickers = args.ticker if args.ticker else load_watchlist(args.watchlist)
    fetch_fn = None
    if args.source == "edgar":
        from traders.edgar_fundamentals import _default_edgar_facts_fetcher

        try:
            fetch_fn = _default_edgar_facts_fetcher()
        except RuntimeError as e:
            conn.close()
            raise SystemExit(str(e)) from e
    try:
        result = ingest_fundamental_periods(
            conn, tickers, fetch_fn=fetch_fn, source=args.source, delay_s=args.delay
        )
    except ImportError as e:
        conn.close()
        raise SystemExit(str(e)) from e
    written = result["written"]
    skipped = result["skipped"]
    print(
        f"ingested {result['periods']} period(s) across {len(written)} ticker(s); "
        f"{len(skipped)} skipped (source: {args.source})"
    )
    for ticker in written:
        print(f"  {ticker}")
    if skipped:
        print(f"  skipped: {', '.join(skipped)}")
    conn.close()


def _add_eval_llm(sub: argparse._SubParsersAction) -> None:
    eval_p = sub.add_parser(
        "eval-llm",
        help="Score the LLM generators against fixture cases "
        "(needs the 'llm' extra + ANTHROPIC_API_KEY)",
    )
    eval_p.add_argument(
        "--generator",
        choices=("thesis", "postmortem", "both"),
        default="both",
        help="Which LLM generator(s) to evaluate (default: both)",
    )
    eval_p.add_argument(
        "--min-pass-rate",
        type=float,
        default=None,
        help="If set, exit non-zero when the overall pass rate is below this (0-1)",
    )
    eval_p.add_argument(
        "--format",
        dest="fmt",
        choices=("text", "markdown"),
        default="text",
        help="Output format (default: text)",
    )
    eval_p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the report(s) to this path instead of stdout",
    )


def _run_eval_llm(args: argparse.Namespace) -> None:
    from traders.eval_llm import (
        default_post_mortem_cases,
        default_thesis_cases,
        evaluate_post_mortem_generator,
        evaluate_thesis_generator,
        render_report,
    )

    reports = []
    parts = []
    with _llm_extra_guard(True):
        if args.generator in ("thesis", "both"):
            from traders.llm_thesis import LLMThesisGenerator

            rep = evaluate_thesis_generator(LLMThesisGenerator(), default_thesis_cases())
            reports.append(rep)
            parts.append(render_report(rep, "thesis generator", args.fmt))
        if args.generator in ("postmortem", "both"):
            from traders.llm_postmortem import LLMPostMortemGenerator

            rep = evaluate_post_mortem_generator(
                LLMPostMortemGenerator(), default_post_mortem_cases()
            )
            reports.append(rep)
            parts.append(render_report(rep, "post-mortem generator", args.fmt))
    _emit("\n".join(parts), args.output)
    if args.min_pass_rate is not None:
        worst = min((r.pass_rate for r in reports), default=1.0)
        if worst < args.min_pass_rate:
            print(f"eval below threshold: {worst:.2f} < {args.min_pass_rate:.2f}")
            raise SystemExit(1)


COMMANDS: list[Command] = [
    Command("universe", _add_universe, _run_universe),
    Command("ingest-prices", _add_ingest_prices, _run_ingest_prices),
    Command("ingest-fundamentals", _add_ingest_fundamentals, _run_ingest_fundamentals),
    Command(
        "ingest-fundamental-periods",
        _add_ingest_fundamental_periods,
        _run_ingest_fundamental_periods,
    ),
    Command("eval-llm", _add_eval_llm, _run_eval_llm),
]
