"""Quality scoring — Piotroski F-score over the period-by-period fundamentals.

The first consumer of the slice-33 statement *series* (:mod:`traders.fundamental_periods`):
a deterministic, look-ahead-safe quality score. The value path will gate on it
(backlog B4) so a "cheap" name must also be **improving and financially sound**
rather than a value trap — the persona review's sharpest criticism.

Piotroski (2000) F-score: nine binary tests across profitability, leverage /
liquidity, and operating efficiency; higher = higher quality. We compute it from
a ticker's current and prior **annual** periods. Each test returns ``None`` when
its inputs are missing, so a sparse statement yields a *partial* score with a
known denominator (``computable``) instead of a silently wrong 0-of-9 — a
consumer can then demand both a minimum score and a minimum number of computable
tests.

Look-ahead safety is inherited from the store: :func:`piotroski_for` pulls the two
most recent periods *available as of* the decision date via
:func:`traders.fundamental_periods.latest_periods`, so a historical rebalance can
never use a statement filed later. This is a leaf-ish analysis module — it imports
only the period type/accessor, never an agent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from traders.fundamental_periods import (
    ANNUAL_REPORTING_LAG_DAYS,
    QUARTERLY_REPORTING_LAG_DAYS,
    FundamentalPeriod,
    latest_periods,
)

# The nine tests, in Piotroski's grouping order — also the component-dict keys.
F_COMPONENTS = (
    "roa_positive",
    "cfo_positive",
    "roa_improving",
    "accruals",
    "leverage_decreasing",
    "current_ratio_improving",
    "no_dilution",
    "gross_margin_improving",
    "asset_turnover_improving",
)


@dataclass(frozen=True)
class PiotroskiScore:
    """A Piotroski F-score plus how many of the nine tests were computable.

    ``score`` counts only tests that scored 1; ``computable`` counts tests whose
    inputs were present (1 *or* 0). A consumer wanting confidence should require
    both a high ``score`` and a high ``computable`` — a 3/3 is not a 9/9.
    """

    score: int
    computable: int
    components: dict[str, int | None]


def _ratio(num: float | None, den: float | None) -> float | None:
    """``num / den`` or None when an input is missing or the denominator is zero."""
    if num is None or den is None or den == 0:
        return None
    return num / den


def _ratio_pos(num: float | None, den: float | None) -> float | None:
    """``num / den`` requiring a strictly positive denominator (else None).

    For return ratios (ROIC, ROE) a zero or negative base — e.g. negative equity
    from accumulated losses — makes the ratio meaningless, so we drop it rather
    than report a misleading number.
    """
    if num is None or den is None or den <= 0:
        return None
    return num / den


def _roa(p: FundamentalPeriod) -> float | None:
    return _ratio(p.net_income, p.total_assets)


def _current_ratio(p: FundamentalPeriod) -> float | None:
    return _ratio(p.current_assets, p.current_liabilities)


def _leverage(p: FundamentalPeriod) -> float | None:
    return _ratio(p.long_term_debt, p.total_assets)


def _gross_margin(p: FundamentalPeriod) -> float | None:
    return _ratio(p.gross_profit, p.revenue)


def _asset_turnover(p: FundamentalPeriod) -> float | None:
    return _ratio(p.revenue, p.total_assets)


def _pos(x: float | None) -> int | None:
    """1 if strictly positive, 0 if not, None when missing."""
    return None if x is None else int(x > 0)


def _gt(a: float | None, b: float | None) -> int | None:
    """1 if ``a > b``, 0 if not, None when either is missing."""
    return None if a is None or b is None else int(a > b)


def _lt(a: float | None, b: float | None) -> int | None:
    return None if a is None or b is None else int(a < b)


def _le(a: float | None, b: float | None) -> int | None:
    return None if a is None or b is None else int(a <= b)


def piotroski_components(cur: FundamentalPeriod, prev: FundamentalPeriod) -> dict[str, int | None]:
    """The nine Piotroski tests for a current-vs-prior period pair (1 / 0 / None).

    Profitability: ROA positive, operating cash flow positive, ROA improving, and
    accruals (operating cash flow exceeds net income — earnings backed by cash).
    Leverage / liquidity: long-term-debt-to-assets falling, current ratio rising,
    no share dilution. Operating efficiency: gross margin rising, asset turnover
    rising. Each is ``None`` when an input it needs is absent.
    """
    return {
        "roa_positive": _pos(_roa(cur)),
        "cfo_positive": _pos(cur.operating_cash_flow),
        "roa_improving": _gt(_roa(cur), _roa(prev)),
        "accruals": (
            None
            if cur.operating_cash_flow is None or cur.net_income is None
            else int(cur.operating_cash_flow > cur.net_income)
        ),
        "leverage_decreasing": _lt(_leverage(cur), _leverage(prev)),
        "current_ratio_improving": _gt(_current_ratio(cur), _current_ratio(prev)),
        "no_dilution": _le(cur.shares_outstanding, prev.shares_outstanding),
        "gross_margin_improving": _gt(_gross_margin(cur), _gross_margin(prev)),
        "asset_turnover_improving": _gt(_asset_turnover(cur), _asset_turnover(prev)),
    }


def piotroski_score(cur: FundamentalPeriod, prev: FundamentalPeriod) -> PiotroskiScore:
    """Score a current-vs-prior annual pair into a :class:`PiotroskiScore`."""
    components = piotroski_components(cur, prev)
    present = [v for v in components.values() if v is not None]
    return PiotroskiScore(
        score=sum(present),
        computable=len(present),
        components=components,
    )


@dataclass(frozen=True)
class QualityMetrics:
    """Return-on-capital and margin *levels* plus their year-over-year change.

    Complements the binary Piotroski tests with the magnitudes a long-term owner
    actually weighs — how profitably the business compounds capital, and whether
    that is improving. ``roic`` uses net income over invested capital (equity +
    long-term debt) as a simple, statement-only proxy — not a tax-adjusted NOPAT
    ROIC. Any field is ``None`` when its inputs are missing.
    """

    roic: float | None
    roe: float | None
    gross_margin: float | None
    roic_delta: float | None
    roe_delta: float | None
    gross_margin_delta: float | None


def _invested_capital(p: FundamentalPeriod) -> float | None:
    """Equity + long-term debt (a statement-only invested-capital proxy)."""
    if p.total_equity is None:
        return None
    debt = p.long_term_debt if p.long_term_debt is not None else 0.0
    return p.total_equity + debt


def _roic(p: FundamentalPeriod) -> float | None:
    return _ratio_pos(p.net_income, _invested_capital(p))


def _roe(p: FundamentalPeriod) -> float | None:
    return _ratio_pos(p.net_income, p.total_equity)


def _delta(cur: float | None, prev: float | None) -> float | None:
    return None if cur is None or prev is None else cur - prev


def quality_metrics(
    cur: FundamentalPeriod, prev: FundamentalPeriod | None = None
) -> QualityMetrics:
    """ROIC / ROE / gross-margin levels for ``cur``, with deltas vs ``prev`` if given."""
    roic, roe, gross_margin = _roic(cur), _roe(cur), _gross_margin(cur)
    if prev is None:
        return QualityMetrics(roic, roe, gross_margin, None, None, None)
    return QualityMetrics(
        roic,
        roe,
        gross_margin,
        _delta(roic, _roic(prev)),
        _delta(roe, _roe(prev)),
        _delta(gross_margin, _gross_margin(prev)),
    )


def quality_metrics_for(
    conn: sqlite3.Connection,
    ticker: str,
    *,
    as_of: str,
    annual_lag_days: int = ANNUAL_REPORTING_LAG_DAYS,
    quarterly_lag_days: int = QUARTERLY_REPORTING_LAG_DAYS,
) -> QualityMetrics | None:
    """Quality metrics for ``ticker`` from the annual periods public as of ``as_of``.

    Uses the latest period for levels and the one before it for deltas (look-ahead
    -safe); ``None`` only when the ticker has no annual period yet.
    """
    periods = latest_periods(
        conn,
        ticker,
        as_of=as_of,
        period_type="annual",
        limit=2,
        annual_lag_days=annual_lag_days,
        quarterly_lag_days=quarterly_lag_days,
    )
    if not periods:
        return None
    return quality_metrics(periods[0], periods[1] if len(periods) > 1 else None)


def piotroski_for(
    conn: sqlite3.Connection,
    ticker: str,
    *,
    as_of: str,
    annual_lag_days: int = ANNUAL_REPORTING_LAG_DAYS,
    quarterly_lag_days: int = QUARTERLY_REPORTING_LAG_DAYS,
) -> PiotroskiScore | None:
    """Piotroski F-score for ``ticker`` from the latest two annual periods public
    as of ``as_of`` (look-ahead-safe), or None when fewer than two are available.
    """
    periods = latest_periods(
        conn,
        ticker,
        as_of=as_of,
        period_type="annual",
        limit=2,
        annual_lag_days=annual_lag_days,
        quarterly_lag_days=quarterly_lag_days,
    )
    if len(periods) < 2:
        return None
    return piotroski_score(periods[0], periods[1])


def quality_scores_asof(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    annual_lag_days: int = ANNUAL_REPORTING_LAG_DAYS,
    quarterly_lag_days: int = QUARTERLY_REPORTING_LAG_DAYS,
) -> dict[str, PiotroskiScore]:
    """Piotroski score per ticker that has one as of ``as_of`` (look-ahead-safe).

    The bulk companion to :func:`piotroski_for` — the live ``analyse`` / ``run-daily``
    path resolves every name's score once and hands the map to the
    ``SignalThesisGenerator``. Tickers without two public annual periods are simply
    absent from the result.
    """
    tickers = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT ticker FROM fundamental_periods WHERE period_type = 'annual'"
        )
    ]
    out: dict[str, PiotroskiScore] = {}
    for ticker in tickers:
        score = piotroski_for(
            conn,
            ticker,
            as_of=as_of,
            annual_lag_days=annual_lag_days,
            quarterly_lag_days=quarterly_lag_days,
        )
        if score is not None:
            out[ticker] = score
    return out
