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

Slice 35 (B4) adds an optional ``quality`` map (per-ticker Piotroski F-score from
the slice-33/34 period series, resolved look-ahead-safe). It gates *only* the
value thesis: a cheap name with a confirmed weak F-score is vetoed (a value
trap), a confirmed-strong one is conviction-boosted, and an unknown or too-sparse
score falls back to the cheap-only thesis — so, like the ``fundamentals`` path,
it is additive (no ``quality`` => unchanged).

Slice 36 (B3) adds the third value leg: an optional ``valuation`` map (per-ticker
intrinsic value / margin of safety from :mod:`traders.valuation`). A cheap, sound
name trading without a margin of safety to intrinsic value is vetoed, and when an
estimate is present margin of safety — not flag count — drives the value thesis's
conviction (quality can still nudge it up by one). Also additive: no ``valuation``
=> slice-35 behaviour.

Slice 42 (B10) extends the ``quality`` map to the *price* signals: a momentum or
mean-reversion long on a confirmed-weak-quality company has its conviction capped,
so a big price move on a shaky business no longer reads as high conviction. Still
additive (no ``quality`` => unchanged). PEAD/SUE (needs consensus estimates)
remains deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from traders.fundamentals import Fundamentals
from traders.prices import PriceHistory
from traders.quality import PiotroskiScore
from traders.signals import DraftThesis
from traders.valuation import DEFAULT_MIN_MARGIN_OF_SAFETY, IntrinsicValue
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

# Margin-of-safety conviction bands (slice 36): the deeper the discount to
# intrinsic value, the higher the conviction a value thesis earns.
_MOS_STRONG = 0.50  # >=50% below intrinsic value -> conviction 5
_MOS_GOOD = 0.35  # >=35% -> conviction 4 (else 3, down to the gate minimum)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _mos_conviction(margin_of_safety: float) -> int:
    """Conviction (3–5) from a value thesis's margin of safety (slice 36)."""
    if margin_of_safety >= _MOS_STRONG:
        return 5
    if margin_of_safety >= _MOS_GOOD:
        return 4
    return 3


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
    # Slice 35 (B4) — Piotroski quality gate on the value thesis (optional; absent
    # => value behaves exactly as slice 26). A cheap name with a *confirmed* low
    # F-score is a value trap and is vetoed; a confirmed-strong one is conviction-
    # boosted; an unknown / too-sparse score falls back to the cheap-only thesis.
    quality: dict[str, PiotroskiScore] | None = None
    min_piotroski_score: int = 5  # gate: F-score >= 5 to survive the quality check
    min_piotroski_computable: int = 5  # need >=5 of 9 tests to judge quality at all
    strong_piotroski_score: int = 7  # at/above this, bump value conviction by one
    # Slice 36 (B3) — margin-of-safety gate on the value thesis (optional; absent
    # => value behaves as slice 35). A cheap, sound name with no margin of safety
    # vs its intrinsic value is vetoed, and margin of safety — not flag count —
    # drives the value thesis's conviction when an estimate is available.
    valuation: dict[str, IntrinsicValue] | None = None
    min_margin_of_safety: float = DEFAULT_MIN_MARGIN_OF_SAFETY  # gate: need >= this MoS
    # Slice 42 (B10) — fold quality into the price-signal convictions: a strong
    # momentum / mean-reversion signal on a financially weak company (confirmed low
    # Piotroski) is capped, so conviction reflects the business, not just the size
    # of the price move. Additive: no `quality` => unchanged.
    weak_quality_conviction_cap: int = 2

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
            conviction, qnote = self._quality_modulate(ticker, self._momentum_conviction(mom))
            return [
                DraftThesis(
                    thesis_type="momentum",
                    direction="long",
                    conviction=conviction,
                    suggested_size_pct=size,
                    exit_condition=f"Exit on 12-1 momentum turning negative or {_STOP}.",
                    rationale=(
                        f"[signal] 12-1 momentum {mom:+.1f}% (RSI {_fmt(r)}); "
                        f"trend-continuation long.{note}{qnote}"
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
            conviction, qnote = self._quality_modulate(ticker, self._meanrev_conviction(mr, r))
            return [
                DraftThesis(
                    thesis_type="mean-reversion",
                    direction="long",
                    conviction=conviction,
                    suggested_size_pct=size,
                    exit_condition=(
                        f"Exit when price reverts to its 20-day mean (z>=0) or {_STOP}."
                    ),
                    rationale=(
                        f"[signal] oversold: 20-day z {_fmt(mr)}, RSI {_fmt(r)}; "
                        f"mean-reversion bounce long.{note}{qnote}"
                    ),
                )
            ]
        return []

    def _quality_modulate(self, ticker: str, base_conviction: int) -> tuple[int, str]:
        """Cap a price-signal conviction when the company's quality is confirmed weak.

        A momentum or mean-reversion long on a name with a confirmed low Piotroski
        F-score is a strong *price* signal on a shaky *business* — so cap its
        conviction (and say why). Unknown or sufficient quality leaves it unchanged,
        keeping the price-only path additive (no ``quality`` map => byte-identical).
        """
        q = self.quality.get(ticker) if self.quality else None
        if q is None or q.computable < self.min_piotroski_computable:
            return base_conviction, ""
        if q.score < self.min_piotroski_score:
            capped = min(base_conviction, self.weak_quality_conviction_cap)
            return capped, f" Weak balance sheet (Piotroski {q.score}/9) — conviction capped."
        return base_conviction, ""

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
        base_conviction = 4 if flags == 3 else 3
        # Margin-of-safety leg (slice 36 / B3): may veto and (re)set the base.
        mos_ok, base_conviction, mos_note = self._apply_value_gate(ticker, base_conviction)
        if not mos_ok:
            return None  # cheap and sound, but no discount to intrinsic value
        # Quality leg (slice 35 / B4): may veto and nudge conviction up.
        quality_ok, conviction, quality_note = self._apply_quality_gate(ticker, base_conviction)
        if not quality_ok:
            return None  # cheap but a confirmed weak balance sheet -> value trap
        return DraftThesis(
            thesis_type="value",
            direction="long",
            conviction=conviction,
            suggested_size_pct=size,
            exit_condition=f"Exit when the valuation re-rates (cheap flags lapse) or {_STOP}.",
            rationale=(
                f"[signal] cheap: E/P {_fmt_pct(ey)}, B/P {_fmt(bp)}, "
                f"FCF yield {_fmt_pct(fy)} ({flags}/3 value flags); value long."
                f"{mos_note}{quality_note}{note}"
            ),
        )

    def _apply_value_gate(self, ticker: str, base_conviction: int) -> tuple[bool, int, str]:
        """Decide a cheap name's fate from its margin of safety (slice 36 / B3).

        Returns ``(passes, conviction, mos_note)``. With no valuation map or no
        estimate for ``ticker`` the name passes unchanged (additive fallback). With
        an estimate it gates: a margin of safety below the minimum is vetoed (cheap
        and sound, but the price isn't far enough below intrinsic value), and
        otherwise margin of safety — not flag count — sets the conviction.
        """
        v = self.valuation.get(ticker) if self.valuation else None
        if v is None:
            return True, base_conviction, ""
        if v.margin_of_safety < self.min_margin_of_safety:
            return False, base_conviction, ""
        note = (
            f" Intrinsic value ~${v.iv_per_share:.0f}/sh; margin of safety "
            f"{v.margin_of_safety * 100:.0f}% ({v.years}yr owner earnings; "
            f"r {v.discount_rate * 100:.0f}%, g {v.terminal_growth * 100:.0f}%); "
            f"price implies {v.implied_growth * 100:.0f}% growth."
        )
        return True, _mos_conviction(v.margin_of_safety), note

    def _apply_quality_gate(self, ticker: str, base_conviction: int) -> tuple[bool, int, str]:
        """Decide a cheap name's fate from its Piotroski score (slice 35 / B4).

        Returns ``(passes, conviction, quality_note)``. With no quality map, no
        entry for ``ticker``, or too few computable tests, the name *passes*
        unchanged (additive fallback — backtests and snapshot-only runs behave
        exactly as slice 26). With enough computable tests it gates: a sub-threshold
        F-score is vetoed (a cheap, deteriorating name is a value trap), and a
        strong score bumps conviction by one (capped at 5).
        """
        q = self.quality.get(ticker) if self.quality else None
        if q is None or q.computable < self.min_piotroski_computable:
            return True, base_conviction, ""
        if q.score < self.min_piotroski_score:
            return False, base_conviction, ""
        conviction = base_conviction
        if q.score >= self.strong_piotroski_score:
            conviction = min(5, base_conviction + 1)
        return True, conviction, f" Quality: Piotroski {q.score}/9 ({q.computable} tests)."

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
    from traders.quality import quality_scores_asof
    from traders.valuation import valuations_asof

    when = as_of or date.today()
    history = load_history_from_db(conn)
    kwargs.setdefault("fundamentals", load_fundamentals_asof(conn, as_of=when.isoformat()))
    kwargs.setdefault("quality", quality_scores_asof(conn, as_of=when.isoformat()))
    kwargs.setdefault("valuation", valuations_asof(conn, history, as_of=when))
    return SignalThesisGenerator(history=history, as_of=when, **kwargs)
