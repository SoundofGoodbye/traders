"""Signal-driven thesis generator — replaces the canned stub.

Implements the existing ``ThesisGenerator`` protocol, so it drops into the
Analyst with no agent changes. Where the stub emitted a fixed conviction-3 long
that ignored the data, this maps the slice-18 signals onto real ``DraftThesis``
fields, per candidate.

It is constructed with a ``PriceHistory`` and an ``as_of`` date; every signal
reads only closes strictly *before* ``as_of`` (via ``closes_before``), so it is
look-ahead-safe and deterministic. Long-only in v1 (paper, no shorts): a name
with no actionable long signal yields no thesis (an empty list), exactly as the
protocol allows. Value / quality / catalyst families wait for fundamentals
(slice 25); this slice ships price-based momentum and mean-reversion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from traders.prices import PriceHistory
from traders.signals import DraftThesis
from traders.signals_lib import (
    closes_before,
    momentum_12_1,
    realized_vol,
    rsi,
    zscore_meanrev,
)

_STOP = "a -8% stop"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


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

    def generate(self, ticker: str, content: str) -> list[DraftThesis]:
        closes = closes_before(self.history, ticker, self.as_of)
        if len(closes) < self.min_history:
            return []
        mom = momentum_12_1(closes)
        mr = zscore_meanrev(closes, 20)
        r = rsi(closes, 14)
        vol = realized_vol(closes, 21)
        size = self._vol_scaled_size(vol)

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
                        "trend-continuation long."
                    ),
                )
            ]

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
                        "mean-reversion bounce long."
                    ),
                )
            ]
        return []

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


def build_signal_generator(
    conn, as_of: date | None = None, **kwargs
) -> SignalThesisGenerator:
    """Build a generator from the DB's prices table (CLI/orchestrator helper)."""
    from traders.prices import load_history_from_db

    history = load_history_from_db(conn)
    return SignalThesisGenerator(history=history, as_of=as_of or date.today(), **kwargs)
