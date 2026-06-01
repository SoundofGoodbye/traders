"""Deterministic, look-ahead-safe signal math — the analysis foundation.

Pure stdlib functions over price series, used by the data-driven Scout and
Analyst (slices 20–21) and computable inside the backtest harness. This is a
*leaf* module: it imports nothing from the agents, only ``statistics`` and the
``PriceHistory`` value object.

Look-ahead is structurally prevented: time-series signals operate on a plain
list of closes (oldest→newest), and the only bridge from a ``PriceHistory`` is
``closes_before(history, ticker, as_of)``, which returns closes strictly *before*
``as_of``. A signal can never see the decision day's or a future close because it
is never handed one.

The math is public-domain: price momentum (Jegadeesh–Titman), realized
volatility, mean-reversion z-score, and Wilder's RSI. Cross-sectional helpers
(``winsorize`` / ``zscore``) standardize a single day's cross-section, computing
their thresholds only from the values present — never ex-post.
"""

from __future__ import annotations

from datetime import date
from statistics import fmean, pstdev

from traders.prices import PriceHistory

# Trading-day approximations (a calendar year is ~252 trading days, a month ~21).
_TRADING_MONTH = 21
_TRADING_YEAR = 252


def closes_before(history: PriceHistory, ticker: str, as_of: date) -> list[float]:
    """Closes for ``ticker`` strictly before ``as_of`` (oldest→newest).

    The single look-ahead gate: callers feed the returned list to the signal
    functions, so no signal can observe the ``as_of`` day or any later close.
    """
    series = history.series.get(ticker)
    if not series:
        return []
    cutoff = as_of.isoformat()
    return [close for day, close in series if day < cutoff]


def momentum(closes: list[float], lookback: int, skip: int = 0) -> float | None:
    """Return (%) over the window ending ``skip`` bars before the last close.

    ``skip`` excludes the most recent bars (the classic momentum design skips
    the last month to dodge short-term reversal). Returns ``None`` when there is
    not enough history.
    """
    need = lookback + skip + 1
    if len(closes) < need:
        return None
    recent = closes[-1 - skip]
    base = closes[-1 - skip - lookback]
    if base == 0:
        return None
    return (recent / base - 1.0) * 100.0


def momentum_12_1(closes: list[float]) -> float | None:
    """12-month-minus-1-month price momentum, with a 6-1 fallback.

    Measures the run from ~12 months ago to ~1 month ago. Falls back to a 6-1
    window when there isn't a full year of history, and ``None`` below that.
    """
    full = momentum(closes, lookback=_TRADING_YEAR - _TRADING_MONTH, skip=_TRADING_MONTH)
    if full is not None:
        return full
    return momentum(closes, lookback=5 * _TRADING_MONTH, skip=_TRADING_MONTH)


def realized_vol(closes: list[float], window: int = _TRADING_MONTH) -> float | None:
    """Daily realized volatility: stdev of simple returns over the last ``window``.

    A fraction (e.g. ``0.018`` = 1.8%/day), used to risk-scale position size.
    ``None`` when there isn't a full window of returns.
    """
    if len(closes) < window + 1:
        return None
    tail = closes[-(window + 1) :]
    returns = [tail[i] / tail[i - 1] - 1.0 for i in range(1, len(tail)) if tail[i - 1] != 0]
    if len(returns) < 2:
        return None
    return pstdev(returns)


def zscore_meanrev(closes: list[float], window: int = 20) -> float | None:
    """Z-score of the last close vs its trailing-``window`` mean.

    Positive = stretched above the mean (overbought); negative = oversold. A
    flat window (stdev 0) scores 0. ``None`` below a full window.
    """
    if len(closes) < window:
        return None
    win = closes[-window:]
    mu = fmean(win)
    sd = pstdev(win)
    if sd == 0:
        return 0.0
    return (closes[-1] - mu) / sd


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's Relative Strength Index (0–100). ``None`` below ``period+1`` bars.

    An all-up run → 100 (no average loss); an all-down run → 0.
    """
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _quantile(sorted_xs: list[float], p: float) -> float:
    idx = int(round(p * (len(sorted_xs) - 1)))
    idx = min(len(sorted_xs) - 1, max(0, idx))
    return sorted_xs[idx]


def winsorize(values: list[float | None], lo: float = 0.05, hi: float = 0.95) -> list[float | None]:
    """Clamp each value to the ``[lo, hi]`` quantiles of the present values.

    Thresholds come only from the values given (the day's cross-section) — never
    from out-of-sample data. ``None`` entries pass through, preserving position.
    """
    present = sorted(v for v in values if v is not None)
    if not present:
        return list(values)
    low = _quantile(present, lo)
    high = _quantile(present, hi)
    return [None if v is None else min(max(v, low), high) for v in values]


def zscore(values: list[float | None]) -> list[float | None]:
    """Standardize a cross-section to mean 0 / stdev 1, preserving ``None`` slots.

    Fewer than two present values, or a zero-stdev cross-section, yields 0 for
    every present value (no information to spread).
    """
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [None if v is None else 0.0 for v in values]
    mu = fmean(present)
    sd = pstdev(present)
    if sd == 0:
        return [None if v is None else 0.0 for v in values]
    return [None if v is None else (v - mu) / sd for v in values]


# --- Fundamental signals (slice 26) ---------------------------------------
# Scalar ratios over a point-in-time fundamentals snapshot joined with the price.
# These take already-as-of-safe inputs — the caller passes the snapshot resolved
# by ``fundamentals.latest_fundamentals(as_of=...)`` and the last close *before*
# the decision day — so look-ahead is prevented the same way the price signals'
# ``closes_before`` gate prevents it. Higher value yields = cheaper. Each returns
# ``None`` when an input is missing or the denominator is non-positive, so a
# sparse snapshot degrades gracefully instead of raising.


def earnings_yield(trailing_eps: float | None, price: float | None) -> float | None:
    """Trailing earnings yield E/P (= EPS / price). Can be negative (loss-making)."""
    if trailing_eps is None or not price or price <= 0:
        return None
    return trailing_eps / price


def book_to_price(book_value_per_share: float | None, price: float | None) -> float | None:
    """Book-to-price B/P (= book value per share / price). Higher = cheaper on assets."""
    if book_value_per_share is None or not price or price <= 0:
        return None
    return book_value_per_share / price


def fcf_yield(free_cash_flow: float | None, market_cap: float | None) -> float | None:
    """Free-cash-flow yield (= FCF / market cap). Higher = cheaper on cash generation."""
    if free_cash_flow is None or not market_cap or market_cap <= 0:
        return None
    return free_cash_flow / market_cap


def days_to_earnings(next_earnings_date: str | None, as_of: date) -> int | None:
    """Calendar days from ``as_of`` to the next earnings date.

    ``None`` when the date is missing or unparseable; negative when the date is
    already in the past (a stale snapshot). Used as an event-risk annotation, not
    a directional signal — a date alone says nothing about direction.
    """
    if not next_earnings_date:
        return None
    try:
        nxt = date.fromisoformat(next_earnings_date[:10])
    except ValueError:
        return None
    return (nxt - as_of).days
