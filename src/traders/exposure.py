"""Portfolio exposure — concentration and hidden correlation across open positions.

Backlog item B14: the PM does a concentration check at decision time, but nothing
shows the *standing* shape of the book. ``exposure_report`` summarizes how
concentrated the open positions are (largest name, top-3, a Herfindahl index) and
surfaces pairs that move in lockstep — the "you think you're diversified but these
are the same bet" risk a position-size table alone hides.

Concentration reads only the ``positions`` table; correlation reads the price
history (Pearson over recent overlapping daily returns). Sector exposure is
deferred — it needs per-ticker sector data the system doesn't ingest yet. Pure
stdlib (``statistics.correlation``); no migration, no new dependency.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date

from traders.prices import PriceHistory, load_history_from_db

DEFAULT_WINDOW = 63  # ~one quarter of trading days
DEFAULT_MIN_CORR = 0.8
DEFAULT_MIN_OVERLAP = 20


@dataclass(frozen=True)
class Concentration:
    num_positions: int
    total_size_pct: float
    largest_ticker: str | None
    largest_pct: float
    top3_pct: float
    herfindahl: float  # sum of squared normalized weights (1.0 = a single name)


@dataclass(frozen=True)
class CorrelatedPair:
    a: str
    b: str
    correlation: float


@dataclass(frozen=True)
class ExposureReport:
    concentration: Concentration
    correlated_pairs: list[CorrelatedPair]


def open_position_weights(conn: sqlite3.Connection) -> list[tuple[str, float]]:
    """``(ticker, size_pct)`` for each open position."""
    rows = conn.execute(
        "SELECT ticker, size_pct FROM positions WHERE status = 'open' ORDER BY ticker"
    ).fetchall()
    return [(t, float(s)) for t, s in rows]


def concentration(weights: list[tuple[str, float]]) -> Concentration:
    """Summarize how concentrated a set of ``(ticker, size_pct)`` weights is."""
    if not weights:
        return Concentration(0, 0.0, None, 0.0, 0.0, 0.0)
    total = sum(s for _, s in weights)
    ranked = sorted(weights, key=lambda ws: -ws[1])
    hhi = sum((s / total) ** 2 for _, s in weights) if total > 0 else 0.0
    return Concentration(
        num_positions=len(weights),
        total_size_pct=total,
        largest_ticker=ranked[0][0],
        largest_pct=ranked[0][1],
        top3_pct=sum(s for _, s in ranked[:3]),
        herfindahl=hhi,
    )


def _returns_by_day(history: PriceHistory, ticker: str, as_of: date) -> dict[str, float]:
    """Daily simple returns keyed by day, for closes strictly before ``as_of``."""
    cutoff = as_of.isoformat()
    series = sorted((d, c) for d, c in (history.series.get(ticker) or ()) if d < cutoff)
    out: dict[str, float] = {}
    for (_, prev), (day, close) in zip(series, series[1:]):
        if prev:
            out[day] = close / prev - 1.0
    return out


def correlations(
    history: PriceHistory,
    tickers: list[str],
    *,
    as_of: date,
    window: int = DEFAULT_WINDOW,
    min_corr: float = DEFAULT_MIN_CORR,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> list[CorrelatedPair]:
    """Pairs whose recent daily returns are correlated at ``>= min_corr`` (abs)."""
    rets = {t: _returns_by_day(history, t, as_of) for t in tickers}
    pairs: list[CorrelatedPair] = []
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            a, b = tickers[i], tickers[j]
            common = sorted(set(rets[a]) & set(rets[b]))[-window:]
            if len(common) < min_overlap:
                continue
            try:
                corr = statistics.correlation(
                    [rets[a][d] for d in common], [rets[b][d] for d in common]
                )
            except statistics.StatisticsError:
                continue  # a flat series has no correlation
            if abs(corr) >= min_corr:
                pairs.append(CorrelatedPair(a, b, corr))
    pairs.sort(key=lambda p: -abs(p.correlation))
    return pairs


def exposure_report(
    conn: sqlite3.Connection,
    history: PriceHistory | None = None,
    *,
    as_of: date | None = None,
    window: int = DEFAULT_WINDOW,
    min_corr: float = DEFAULT_MIN_CORR,
) -> ExposureReport:
    """Concentration + correlated-pair report over the current open positions."""
    hist = history if history is not None else load_history_from_db(conn)
    weights = open_position_weights(conn)
    pairs = correlations(
        hist,
        [t for t, _ in weights],
        as_of=as_of or date.today(),
        window=window,
        min_corr=min_corr,
    )
    return ExposureReport(concentration=concentration(weights), correlated_pairs=pairs)
