"""Fill the ``prices`` table from Stooq — free, US+EU, zero extra dependencies.

Stooq serves plain ``Date,Open,High,Low,Close,Volume`` CSV over HTTP, which
stdlib ``urllib`` + ``csv`` parse directly — so price ingestion stays in the
zero-dependency core (no ``realdata`` extra needed). This makes the backtest
harness real-data-capable and feeds the signals library.

The network call is hidden behind an injected ``fetch_csv(symbol) -> str``
closure (real impl uses ``urllib``; tests inject canned CSV), mirroring the
EDGAR adapter. Rows are stored under the **original watchlist ticker** (so the
rest of the system keys consistently) on the **exchange-local ISO trading day**
Stooq reports. ``save_prices`` is ``INSERT OR REPLACE`` so re-runs are idempotent.

Caveats (documented, not hidden): Stooq's free EU coverage is partial — an
unmapped or unavailable name is skipped, never fatal. Closes may be unadjusted
for splits/dividends; the watchlist is point-in-time-today (survivorship bias).
"""

from __future__ import annotations

import csv
import io
import sqlite3
from typing import Callable

from traders.prices import save_prices
from traders.stooq_symbols import to_stooq_symbol

_STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


def _default_csv_fetcher() -> Callable[[str], str]:
    """Build the real Stooq fetcher (stdlib urllib). Network only when called."""
    import urllib.request

    def fetch(symbol: str) -> str:
        url = _STOOQ_URL.format(symbol=symbol)
        req = urllib.request.Request(url, headers={"User-Agent": "traders-paper/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")

    return fetch


def parse_stooq_csv(text: str) -> list[tuple[str, float]]:
    """Parse Stooq daily CSV into ``[(iso_day, close)]``, dropping bad rows.

    Skips the ``N/D`` placeholders and any row without a parseable close.
    """
    out: list[tuple[str, float]] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        day = (row.get("Date") or "").strip()
        raw_close = (row.get("Close") or "").strip()
        if not day or raw_close in ("", "N/D"):
            continue
        try:
            out.append((day, float(raw_close)))
        except ValueError:
            continue
    return out


def ingest_prices(
    conn: sqlite3.Connection,
    tickers: list[str],
    *,
    fetch_csv: Callable[[str], str] | None = None,
    since: str | None = None,
    delay_s: float = 0.0,
    symbol_map: Callable[[str], str | None] = to_stooq_symbol,
) -> dict[str, object]:
    """Fetch daily closes for ``tickers`` into the ``prices`` table.

    Returns ``{"written": {ticker: rows}, "skipped": [ticker, ...]}``. A ticker
    is skipped (never fatal) when it has no Stooq mapping, the fetch fails, or no
    usable rows come back. ``delay_s`` throttles between *network* fetches (set
    it for real Stooq runs; defaults to 0 so injected-fetcher tests stay fast).
    """
    fetch = fetch_csv or _default_csv_fetcher()
    written: dict[str, int] = {}
    skipped: list[str] = []
    for ticker in tickers:
        symbol = symbol_map(ticker)
        if symbol is None:
            skipped.append(ticker)
            continue
        if delay_s > 0:
            import time

            time.sleep(delay_s)
        try:
            text = fetch(symbol)
        except Exception:
            skipped.append(ticker)
            continue
        rows = parse_stooq_csv(text)
        if since is not None:
            rows = [(day, close) for day, close in rows if day >= since]
        if not rows:
            skipped.append(ticker)
            continue
        save_prices(conn, ticker, rows)
        written[ticker] = len(rows)
    return {"written": written, "skipped": skipped}
