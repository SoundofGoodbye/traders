"""Historical price store + abstraction for the backtest harness.

A ``PriceHistory`` is an in-memory, as-of-queryable set of daily closes. The
harness depends only on this abstraction, so the price source is swappable:

* ``synthetic_history`` — a deterministic pseudo-random walk per ticker. No
  network, no DB, reproducible across runs (``hashlib``-seeded, never the
  per-process-salted builtin ``hash``). The hermetic default for tests/demos.
* ``load_history_from_db`` — read the ``prices`` table (migration 006).
* ``save_prices`` — write rows into that table (used by a future ingestor).

ISO date strings compare correctly lexicographically, so as-of lookups are a
plain bisect over sorted ``(day, close)`` pairs. Zero dependencies — stdlib only.
"""

from __future__ import annotations

import bisect
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

# Synthetic-walk shape. Deliberately mild so a 6-month window produces a spread
# of winners and losers rather than everything trending one way.
_SYNTH_VOL = 0.02
_SYNTH_DRIFT = 0.0004
_SYNTH_BASE_LOW = 50.0
_SYNTH_BASE_SPAN = 150.0


@dataclass(frozen=True)
class PriceHistory:
    """Daily closes keyed by ticker, each series sorted oldest-first.

    ``series[ticker]`` is a tuple of ``(iso_day, close)`` pairs in ascending
    day order. Built once, queried many times by the harness.
    """

    series: dict[str, tuple[tuple[str, float], ...]]

    def tickers(self) -> tuple[str, ...]:
        return tuple(self.series.keys())

    def close_asof(self, ticker: str, day: date) -> float | None:
        """Most recent close on or before ``day``; None if none exists."""
        s = self.series.get(ticker)
        if not s:
            return None
        target = day.isoformat()
        # Rightmost entry whose day <= target. (target, inf) sorts after every
        # real (day, close) sharing that day, so bisect_right lands past them.
        idx = bisect.bisect_right(s, (target, float("inf"))) - 1
        if idx < 0:
            return None
        return s[idx][1]


def _unit(*parts: object) -> float:
    """Deterministic float in [0, 1) from the parts. hashlib, not builtin hash."""
    key = ":".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    return int(digest[:8], 16) / 0x100000000


def weekdays(start: date, end: date) -> list[date]:
    """Mon–Fri dates in ``[start, end]`` inclusive (a cheap trading calendar)."""
    out: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def synthetic_history(
    tickers: list[str],
    start: date,
    end: date,
    *,
    vol: float = _SYNTH_VOL,
    drift: float = _SYNTH_DRIFT,
) -> PriceHistory:
    """Deterministic synthetic closes for each ticker over ``[start, end]``.

    Each ticker starts at a ticker-derived base price and walks by a
    ticker+day-derived step. Same inputs always yield the same series.
    """
    days = weekdays(start, end)
    series: dict[str, tuple[tuple[str, float], ...]] = {}
    for ticker in tickers:
        price = _SYNTH_BASE_LOW + _unit(ticker, "base") * _SYNTH_BASE_SPAN
        closes: list[tuple[str, float]] = []
        for i, day in enumerate(days):
            step = (_unit(ticker, i) - 0.5) * 2.0 * vol + drift
            price = max(1.0, price * (1.0 + step))
            closes.append((day.isoformat(), round(price, 4)))
        series[ticker] = tuple(closes)
    return PriceHistory(series=series)


def load_history_from_db(
    conn: sqlite3.Connection,
    tickers: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> PriceHistory:
    """Build a ``PriceHistory`` from the ``prices`` table, oldest-first."""
    clauses: list[str] = []
    params: list[object] = []
    if tickers is not None:
        placeholders = ",".join("?" for _ in tickers)
        clauses.append(f"ticker IN ({placeholders})")
        params.extend(tickers)
    if start is not None:
        clauses.append("day >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("day <= ?")
        params.append(end.isoformat())
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT ticker, day, close FROM prices{where} ORDER BY ticker, day",
        params,
    ).fetchall()
    series: dict[str, list[tuple[str, float]]] = {}
    for ticker, day, close in rows:
        series.setdefault(ticker, []).append((day, float(close)))
    return PriceHistory(series={t: tuple(v) for t, v in series.items()})


def save_prices(
    conn: sqlite3.Connection, ticker: str, closes: list[tuple[str, float]]
) -> int:
    """Upsert ``(day, close)`` rows for one ticker. Returns rows written."""
    conn.executemany(
        "INSERT OR REPLACE INTO prices (ticker, day, close) VALUES (?, ?, ?)",
        [(ticker, day, float(close)) for day, close in closes],
    )
    conn.commit()
    return len(closes)
