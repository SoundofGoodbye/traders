"""Fundamentals store + ingestor — point-in-time-ish snapshots per ticker.

Keeps the prices / price_ingest split in one small module: a typed
``Fundamentals`` row, idempotent ``save_fundamentals`` / ``load_fundamentals``
over the ``fundamentals`` table (migration 007), a look-ahead-safe
``latest_fundamentals`` accessor, and ``ingest_fundamentals`` behind an injected
``fetch_fn`` (real impl = yfinance, behind the ``realdata`` extra; tests inject a
canned fetcher) — mirroring the Stooq ingestor and the yfinance data source.

These feed the slice-26 value / quality / catalyst signals. Ratios are NOT
stored: slice 26 derives E/P, B/P, FCF/P by joining a snapshot's ``trailing_eps``
/ ``book_value_per_share`` / ``free_cash_flow`` / ``market_cap`` against the
``prices`` close, so a snapshot can't bake in a stale price.

Caveat (documented, not hidden): a yfinance ``.info`` reading is a **current**
snapshot, not a historical time series. ``ingest_fundamentals`` stamps it with an
``as_of`` date (default today), so a row is only point-in-time-valid for *that*
date — true historical fundamental backtests need period-by-period statements,
which are deferred.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

_SELECT = (
    "SELECT ticker, as_of, currency, market_cap, trailing_eps,"
    " book_value_per_share, free_cash_flow, shares_outstanding,"
    " next_earnings_date, source FROM fundamentals"
)


@dataclass(frozen=True)
class Fundamentals:
    """One point-in-time fundamental snapshot for a ticker."""

    ticker: str
    as_of: str  # ISO date the snapshot was observed
    currency: str | None
    market_cap: float | None
    trailing_eps: float | None
    book_value_per_share: float | None
    free_cash_flow: float | None
    shares_outstanding: float | None
    next_earnings_date: str | None
    source: str = "yfinance"


def _num(value: Any) -> float | None:
    """Coerce a value to float, or None when it isn't a real number."""
    if isinstance(value, bool):  # bool is an int subclass — not a metric
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _epoch_to_date(value: Any) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    return None


def _next_earnings_date(info: dict[str, Any]) -> str | None:
    """Pull a next-earnings date from either yfinance .info shape, if present."""
    for key in ("earningsTimestamp", "earningsTimestampStart"):
        iso = _epoch_to_date(info.get(key))
        if iso is not None:
            return iso
    return None


def fundamentals_from_info(
    ticker: str,
    info: dict[str, Any],
    *,
    as_of: str,
    source: str = "yfinance",
) -> Fundamentals | None:
    """Map a yfinance ``.info`` dict onto a ``Fundamentals`` row.

    Returns ``None`` when ``info`` is empty or carries no usable numeric field,
    so a flaky / placeholder ticker is skipped rather than stored as noise.
    """
    if not isinstance(info, dict) or not info:
        return None
    row = Fundamentals(
        ticker=ticker,
        as_of=as_of,
        currency=info.get("currency") or info.get("financialCurrency"),
        market_cap=_num(info.get("marketCap")),
        trailing_eps=_num(info.get("trailingEps")),
        book_value_per_share=_num(info.get("bookValue")),
        free_cash_flow=_num(info.get("freeCashflow")),
        shares_outstanding=_num(info.get("sharesOutstanding")),
        next_earnings_date=_next_earnings_date(info),
        source=source,
    )
    numeric = (
        row.market_cap,
        row.trailing_eps,
        row.book_value_per_share,
        row.free_cash_flow,
        row.shares_outstanding,
    )
    if all(v is None for v in numeric):
        return None
    return row


def save_fundamentals(conn: sqlite3.Connection, rows: list[Fundamentals]) -> int:
    """Upsert snapshots, idempotent on ``(ticker, as_of, source)``. Returns rows written."""
    payload = [
        (
            r.ticker,
            r.as_of,
            r.currency,
            r.market_cap,
            r.trailing_eps,
            r.book_value_per_share,
            r.free_cash_flow,
            r.shares_outstanding,
            r.next_earnings_date,
            r.source,
        )
        for r in rows
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO fundamentals"
        " (ticker, as_of, currency, market_cap, trailing_eps, book_value_per_share,"
        " free_cash_flow, shares_outstanding, next_earnings_date, source)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        payload,
    )
    conn.commit()
    return len(payload)


def load_fundamentals(
    conn: sqlite3.Connection,
    ticker: str | None = None,
    source: str | None = None,
) -> list[Fundamentals]:
    """Load snapshots (optionally filtered), newest ``as_of`` first."""
    clauses: list[str] = []
    params: list[object] = []
    if ticker is not None:
        clauses.append("ticker = ?")
        params.append(ticker)
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(f"{_SELECT}{where} ORDER BY as_of DESC, ticker", params).fetchall()
    return [Fundamentals(*r) for r in rows]


def latest_fundamentals(
    conn: sqlite3.Connection,
    ticker: str,
    *,
    as_of: str | None = None,
    source: str | None = None,
) -> Fundamentals | None:
    """Most recent snapshot for ``ticker`` on or before ``as_of`` (look-ahead-safe).

    The ``as_of`` gate is what lets slice 26 read fundamentals at a historical
    rebalance date without peeking at a snapshot observed later.
    """
    clauses = ["ticker = ?"]
    params: list[object] = [ticker]
    if as_of is not None:
        clauses.append("as_of <= ?")
        params.append(as_of)
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    row = conn.execute(
        f"{_SELECT} WHERE {' AND '.join(clauses)} ORDER BY as_of DESC LIMIT 1",
        params,
    ).fetchone()
    return Fundamentals(*row) if row is not None else None


def _default_yf_fetcher() -> Callable[[str], dict[str, Any]]:
    """Build the real yfinance fetcher (ticker -> ``.info`` dict).

    Network only when called. Hard-requires the ``realdata`` extra — mirrors how
    ``YFinanceDataSource`` raises when the package isn't installed.
    """
    try:
        import yfinance
    except ImportError as e:
        raise ImportError(
            "yfinance is not installed. Install with: uv sync --extra realdata"
        ) from e

    def fetch(ticker: str) -> dict[str, Any]:
        info = yfinance.Ticker(ticker).info
        return info if isinstance(info, dict) else {}

    return fetch


def ingest_fundamentals(
    conn: sqlite3.Connection,
    tickers: list[str],
    *,
    fetch_fn: Callable[[str], dict[str, Any]] | None = None,
    as_of: str | None = None,
    source: str = "yfinance",
    delay_s: float = 0.0,
) -> dict[str, object]:
    """Fetch a fundamentals snapshot for each ticker into the ``fundamentals`` table.

    Returns ``{"written": [tickers], "skipped": [tickers]}``. A ticker is skipped
    (never fatal) when the fetch raises or yields no usable fundamentals.
    ``delay_s`` throttles between *network* fetches (set it for real yfinance runs;
    defaults to 0 so injected-fetcher tests stay fast).
    """
    fetch = fetch_fn or _default_yf_fetcher()
    stamp = as_of or date.today().isoformat()
    written: list[str] = []
    skipped: list[str] = []
    rows: list[Fundamentals] = []
    for ticker in tickers:
        if delay_s > 0:
            import time

            time.sleep(delay_s)
        try:
            info = fetch(ticker)
        except Exception:
            skipped.append(ticker)
            continue
        row = fundamentals_from_info(ticker, info, as_of=stamp, source=source)
        if row is None:
            skipped.append(ticker)
            continue
        rows.append(row)
        written.append(ticker)
    if rows:
        save_fundamentals(conn, rows)
    return {"written": written, "skipped": skipped}
