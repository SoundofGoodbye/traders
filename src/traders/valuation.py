"""Intrinsic value & margin of safety — a transparent owner-earnings estimate.

Backlog item B3: completes the value gate (cheap **and** quality **and** a margin
of safety) and lets margin of safety — not flag count — drive conviction. Like
:mod:`traders.quality`, a look-ahead-safe consumer of the slice-33 period series;
the margin-of-safety leg of the ``SignalThesisGenerator`` value thesis reads it.

The model is deliberately simple and assumption-explicit — the review asked for a
*transparent* reverse-DCF / normalized-earnings estimate, not a black box:

  owner earnings   = operating cash flow + capital expenditure (capex is stored
                     negative; netting *all* capex, not just maintenance, is the
                     conservative choice), averaged over the recent annual periods
                     to normalize across the cycle.
  intrinsic value  = owner_earnings * (1 + g) / (r - g)   — Gordon capitalization.
  margin of safety = (iv_per_share - price) / iv_per_share.
  implied growth   = the perpetual growth a buyer at today's price is paying for
                     (reverse-DCF): solving price*shares = oe*(1+g)/(r-g) gives
                     g = (market_value * r - oe) / (oe + market_value).

Defaults are conservative (r = 10%, g = 2%). It returns ``None`` whenever the
inputs can't support an earnings-power estimate (negative or too-sparse owner
earnings, no price, no share count), so the gate degrades gracefully to
cheap+quality only — the same additive contract as the fundamentals/quality paths.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from statistics import fmean

from traders.fundamental_periods import (
    ANNUAL_REPORTING_LAG_DAYS,
    QUARTERLY_REPORTING_LAG_DAYS,
    FundamentalPeriod,
    latest_periods,
)
from traders.prices import PriceHistory
from traders.signals_lib import closes_before

DEFAULT_DISCOUNT_RATE = 0.10
DEFAULT_TERMINAL_GROWTH = 0.02
DEFAULT_NORMALIZATION_YEARS = 5
DEFAULT_MIN_PERIODS = 2
DEFAULT_MIN_MARGIN_OF_SAFETY = 0.20


@dataclass(frozen=True)
class IntrinsicValue:
    """A per-share intrinsic value estimate with its margin of safety and assumptions."""

    owner_earnings: float  # normalized annual owner earnings (total, currency units)
    years: int  # how many annual periods were averaged
    shares: float
    price: float  # the decision-day price it was compared against
    iv_total: float
    iv_per_share: float
    margin_of_safety: float  # (iv_per_share - price) / iv_per_share
    buy_below_price: float  # price at which MoS reaches the required minimum
    implied_growth: float  # perpetual growth the current price already prices in
    discount_rate: float
    terminal_growth: float


def _owner_earnings(p: FundamentalPeriod) -> float | None:
    """Operating cash flow net of capital expenditure (capex stored negative)."""
    if p.operating_cash_flow is None or p.capital_expenditure is None:
        return None
    return p.operating_cash_flow + p.capital_expenditure


def normalized_owner_earnings(
    periods: list[FundamentalPeriod],
    *,
    max_years: int = DEFAULT_NORMALIZATION_YEARS,
    min_periods: int = DEFAULT_MIN_PERIODS,
) -> tuple[float, int] | None:
    """Mean owner earnings over the most recent (up to ``max_years``) annual periods.

    Returns ``(mean, count)`` or ``None`` when fewer than ``min_periods`` periods
    carry the cash-flow lines it needs. ``periods`` is newest-first.
    """
    values = [oe for p in periods[:max_years] if (oe := _owner_earnings(p)) is not None]
    if len(values) < min_periods:
        return None
    return fmean(values), len(values)


def _latest_shares(periods: list[FundamentalPeriod]) -> float | None:
    """The most recent positive share count in a newest-first period list."""
    for p in periods:
        if p.shares_outstanding is not None and p.shares_outstanding > 0:
            return p.shares_outstanding
    return None


def intrinsic_value(
    periods: list[FundamentalPeriod],
    price: float | None,
    *,
    shares: float | None = None,
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH,
    max_years: int = DEFAULT_NORMALIZATION_YEARS,
    min_periods: int = DEFAULT_MIN_PERIODS,
    min_margin_of_safety: float = DEFAULT_MIN_MARGIN_OF_SAFETY,
) -> IntrinsicValue | None:
    """Estimate per-share intrinsic value and margin of safety, or None if not estimable.

    Capitalizes normalized owner earnings at ``(1+g)/(r-g)`` and compares the
    per-share result to ``price``. Returns ``None`` for non-positive owner earnings,
    a missing/non-positive price or share count, or too few periods — every guard a
    graceful degradation, never an exception.
    """
    if not periods or price is None or price <= 0 or discount_rate <= terminal_growth:
        return None
    norm = normalized_owner_earnings(periods, max_years=max_years, min_periods=min_periods)
    if norm is None:
        return None
    owner_earnings, years = norm
    if owner_earnings <= 0:
        return None
    sh = shares if shares is not None else _latest_shares(periods)
    if not sh or sh <= 0:
        return None
    multiple = (1.0 + terminal_growth) / (discount_rate - terminal_growth)
    iv_total = owner_earnings * multiple
    iv_per_share = iv_total / sh
    margin_of_safety = (iv_per_share - price) / iv_per_share
    buy_below_price = iv_per_share * (1.0 - min_margin_of_safety)
    market_value = price * sh
    implied_growth = (market_value * discount_rate - owner_earnings) / (
        owner_earnings + market_value
    )
    return IntrinsicValue(
        owner_earnings=owner_earnings,
        years=years,
        shares=sh,
        price=price,
        iv_total=iv_total,
        iv_per_share=iv_per_share,
        margin_of_safety=margin_of_safety,
        buy_below_price=buy_below_price,
        implied_growth=implied_growth,
        discount_rate=discount_rate,
        terminal_growth=terminal_growth,
    )


def intrinsic_value_for(
    conn: sqlite3.Connection,
    ticker: str,
    *,
    as_of: str,
    price: float | None,
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH,
    max_years: int = DEFAULT_NORMALIZATION_YEARS,
    min_periods: int = DEFAULT_MIN_PERIODS,
    min_margin_of_safety: float = DEFAULT_MIN_MARGIN_OF_SAFETY,
    annual_lag_days: int = ANNUAL_REPORTING_LAG_DAYS,
    quarterly_lag_days: int = QUARTERLY_REPORTING_LAG_DAYS,
) -> IntrinsicValue | None:
    """Intrinsic value for ``ticker`` from the annual periods public as of ``as_of``."""
    periods = latest_periods(
        conn,
        ticker,
        as_of=as_of,
        period_type="annual",
        limit=max_years,
        annual_lag_days=annual_lag_days,
        quarterly_lag_days=quarterly_lag_days,
    )
    return intrinsic_value(
        periods,
        price,
        discount_rate=discount_rate,
        terminal_growth=terminal_growth,
        max_years=max_years,
        min_periods=min_periods,
        min_margin_of_safety=min_margin_of_safety,
    )


def valuations_asof(
    conn: sqlite3.Connection,
    history: PriceHistory,
    *,
    as_of: date,
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
    terminal_growth: float = DEFAULT_TERMINAL_GROWTH,
    max_years: int = DEFAULT_NORMALIZATION_YEARS,
    min_periods: int = DEFAULT_MIN_PERIODS,
    min_margin_of_safety: float = DEFAULT_MIN_MARGIN_OF_SAFETY,
) -> dict[str, IntrinsicValue]:
    """Intrinsic value per ticker that has one as of ``as_of`` (look-ahead-safe).

    The bulk companion the live ``analyse`` / ``run-daily`` path uses: each name's
    price is the last close strictly before ``as_of`` (the same gate the generator
    uses), and its statements are the annual periods public by then. Names that
    can't be valued are simply absent from the result.
    """
    iso = as_of.isoformat()
    tickers = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT ticker FROM fundamental_periods WHERE period_type = 'annual'"
        )
    ]
    out: dict[str, IntrinsicValue] = {}
    for ticker in tickers:
        closes = closes_before(history, ticker, as_of)
        if not closes:
            continue
        iv = intrinsic_value_for(
            conn,
            ticker,
            as_of=iso,
            price=closes[-1],
            discount_rate=discount_rate,
            terminal_growth=terminal_growth,
            max_years=max_years,
            min_periods=min_periods,
            min_margin_of_safety=min_margin_of_safety,
        )
        if iv is not None:
            out[ticker] = iv
    return out
