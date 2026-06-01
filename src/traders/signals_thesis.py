"""Signal-driven thesis generator — replaces the canned stub.

Implements the existing ``ThesisGenerator`` protocol, so it drops into the
Analyst with no agent changes. Where the stub emitted a fixed conviction-3 long
that ignored the data, this maps the slice-18 signals onto real ``DraftThesis``
fields, per candidate.

It is constructed with a ``PriceHistory`` and an ``as_of`` date; every signal
reads only closes strictly *before* ``as_of`` (via ``closes_before``), so it is
look-ahead-safe and deterministic. Long-only in v1 (paper, no shorts): a name
with no actionable long signal yields no thesis (an empty list), exactly as the
protocol allows.

Slice 26 adds an optional ``fundamentals`` lookup (slice-25 snapshots, resolved
look-ahead-safe per ``as_of``). When supplied, a cheap name (value signals:
E/P, B/P, FCF/P) can produce a ``value`` thesis, and an imminent earnings date
annotates whatever thesis fires as event risk. Family priority is fixed in v1:
momentum → value → mean-reversion (a multi-factor composite is a future
refinement). Crucially the fundamental path is **additive**: with no
``fundamentals`` (every historical backtest — snapshots aren't point-in-time
history — and the default tests) behaviour is byte-identical to slice 20.
A full Piotroski quality score (needs period-by-period statements) and PEAD/SUE
(needs consensus estimates) are deferred — neither is computable from a single
snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from traders.fundamentals import Fundamentals
from traders.prices import PriceHistory
from traders.signals import DraftThesis
from traders.signals_lib import (
    book_to_price,
    closes_before,
    days_to_earnings,
    earnings_yield,
    fcf_yield,
    momentum_12_1,
    realized_vol,
    rsi,
    zscore_meanrev,
)

_STOP = "a -8% stop"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


@dataclass(frozen=True)
class SignalThesisGenerator:
    """Deterministic, look-ahead-safe thesis generator over price signals."""

    history: PriceHistory
    as_of: date
    min_history: int = 60
    momentum_threshold_pct: float = 5.0
    oversold_z: float = -1.0
    oversold_rsi: float = 30.0
    base_size_pct: float = 2.0
    target_daily_vol: float = 0.02
    # Slice 26 — fundamentals (optional; absent => price-only, unchanged).
    fundamentals: dict[str, Fundamentals] | None = None
    earnings_yield_min: float = 0.06  # E/P >= 6%  (~ P/E <= 16.7)
    book_to_price_min: float = 0.5  # B/P >= 0.5 (~ P/B <= 2)
    fcf_yield_min: float = 0.05  # FCF/market-cap >= 5%
    value_flags_for_thesis: int = 2  # need >=2 of the 3 cheap flags
    earnings_soon_days: int = 7

    def generate(self, ticker: str, content: str) -> list[DraftThesis]:
        closes = closes_before(self.history, ticker, self.as_of)
        if len(closes) < self.min_history:
            return []
        mom = momentum_12_1(closes)
        mr = zscore_meanrev(closes, 20)
        r = rsi(closes, 14)
        vol = realized_vol(closes, 21)
        size = self._vol_scaled_size(vol)
        price = closes[-1]  # last close strictly before as_of (look-ahead-safe)
        note = self._earnings_note(ticker)

        if mom is not None and mom >= self.momentum_threshold_pct:
            return [
                DraftThesis(
                    thesis_type="momentum",
                    direction="long",
                    conviction=self._momentum_conviction(mom),
                    suggested_size_pct=size,
                    exit_condition=f"Exit on 12-1 momentum turning negative or {_STOP}.",
                    rationale=(
                        f"[signal] 12-1 momentum {mom:+.1f}% (RSI {_fmt(r)}); "
                        f"trend-continuation long.{note}"
                    ),
                )
            ]

        value = self._value_thesis(ticker, price, size, note)
        if value is not None:
            return [value]

        oversold = (mr is not None and mr <= self.oversold_z) or (
            r is not None and r <= self.oversold_rsi
        )
        if oversold:
            return [
                DraftThesis(
                    thesis_type="mean-reversion",
                    direction="long",
                    conviction=self._meanrev_conviction(mr, r),
                    suggested_size_pct=size,
                    exit_condition=(
                        f"Exit when price reverts to its 20-day mean (z>=0) or {_STOP}."
                    ),
                    rationale=(
                        f"[signal] oversold: 20-day z {_fmt(mr)}, RSI {_fmt(r)}; "
                        f"mean-reversion bounce long.{note}"
                    ),
                )
            ]
        return []

    def _value_thesis(
        self, ticker: str, price: float, size: float, note: str
    ) -> DraftThesis | None:
        """A long thesis when a name looks cheap on >= ``value_flags_for_thesis``
        of E/P, B/P, FCF/P. No-op without a fundamentals snapshot for ``ticker`` —
        which keeps the price-only path (backtests, default tests) unchanged.
        """
        if not self.fundamentals:
            return None
        f = self.fundamentals.get(ticker)
        if f is None:
            return None
        ey = earnings_yield(f.trailing_eps, price)
        bp = book_to_price(f.book_value_per_share, price)
        fy = fcf_yield(f.free_cash_flow, f.market_cap)
        flags = sum(
            (
                ey is not None and ey >= self.earnings_yield_min,
                bp is not None and bp >= self.book_to_price_min,
                fy is not None and fy >= self.fcf_yield_min,
            )
        )
        if flags < self.value_flags_for_thesis:
            return None
        return DraftThesis(
            thesis_type="value",
            direction="long",
            conviction=4 if flags == 3 else 3,
            suggested_size_pct=size,
            exit_condition=f"Exit when the valuation re-rates (cheap flags lapse) or {_STOP}.",
            rationale=(
                f"[signal] cheap: E/P {_fmt_pct(ey)}, B/P {_fmt(bp)}, "
                f"FCF yield {_fmt_pct(fy)} ({flags}/3 value flags); value long.{note}"
            ),
        )

    def _earnings_note(self, ticker: str) -> str:
        """' Earnings in Nd — event risk.' when a snapshot shows earnings soon."""
        if not self.fundamentals:
            return ""
        f = self.fundamentals.get(ticker)
        if f is None:
            return ""
        d = days_to_earnings(f.next_earnings_date, self.as_of)
        if d is None or d < 0 or d > self.earnings_soon_days:
            return ""
        return f" Earnings in {d}d — event risk."

    def _momentum_conviction(self, mom: float) -> int:
        if mom >= 25.0:
            return 5
        if mom >= 15.0:
            return 4
        return 3

    def _meanrev_conviction(self, mr: float | None, r: float | None) -> int:
        deep = (mr is not None and mr <= -2.0) or (r is not None and r <= 20.0)
        return 4 if deep else 3

    # Below this daily vol the series is effectively flat; scaling by it would
    # explode size on float noise, so fall back to the base size.
    _VOL_FLOOR = 1e-6

    def _vol_scaled_size(self, vol: float | None) -> float:
        if not vol or vol < self._VOL_FLOOR:
            return round(self.base_size_pct, 1)
        scale = max(0.5, min(2.0, self.target_daily_vol / vol))
        return round(max(0.5, min(5.0, self.base_size_pct * scale)), 1)


def build_signal_generator(conn, as_of: date | None = None, **kwargs) -> SignalThesisGenerator:
    """Build a generator from the DB's prices + fundamentals (CLI/orchestrator helper).

    Resolves each name's fundamentals snapshot look-ahead-safe as of ``as_of`` so
    the live ``analyse`` / ``run-daily`` path can produce value theses once
    ``ingest-fundamentals`` has run. An explicit ``fundamentals`` kwarg wins.
    """
    from traders.fundamentals import load_fundamentals_asof
    from traders.prices import load_history_from_db

    when = as_of or date.today()
    history = load_history_from_db(conn)
    kwargs.setdefault("fundamentals", load_fundamentals_asof(conn, as_of=when.isoformat()))
    return SignalThesisGenerator(history=history, as_of=when, **kwargs)
