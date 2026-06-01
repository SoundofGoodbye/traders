"""Tests for the deterministic signal library (slice 18)."""

from __future__ import annotations

from datetime import date

from traders.prices import PriceHistory
from traders.signals_lib import (
    closes_before,
    momentum,
    momentum_12_1,
    realized_vol,
    rsi,
    winsorize,
    zscore,
    zscore_meanrev,
)


def _hist() -> PriceHistory:
    return PriceHistory(
        series={
            "AAA": (
                ("2026-01-01", 10.0),
                ("2026-01-02", 11.0),
                ("2026-01-03", 12.0),
            )
        }
    )


# ---- look-ahead gate ------------------------------------------------------

def test_closes_before_excludes_as_of_day():
    # The 01-03 close must NOT be visible when deciding on 01-03.
    assert closes_before(_hist(), "AAA", date(2026, 1, 3)) == [10.0, 11.0]


def test_closes_before_is_strictly_prior():
    assert closes_before(_hist(), "AAA", date(2026, 1, 2)) == [10.0]


def test_closes_before_unknown_ticker_is_empty():
    assert closes_before(_hist(), "ZZZ", date(2026, 1, 3)) == []


# ---- momentum -------------------------------------------------------------

def test_momentum_basic_return():
    assert abs(momentum([100.0, 110.0, 121.0], lookback=2) - 21.0) < 1e-9


def test_momentum_skip_excludes_recent_bars():
    assert abs(momentum([100.0, 110.0, 200.0], lookback=1, skip=1) - 10.0) < 1e-9


def test_momentum_insufficient_history_is_none():
    assert momentum([100.0, 110.0], lookback=5) is None


def test_momentum_12_1_falls_back_then_gives_none():
    ramp = [100.0 + i for i in range(130)]  # enough for 6-1, not 12-1
    assert momentum_12_1(ramp) is not None
    assert momentum_12_1([100.0 + i for i in range(100)]) is None


# ---- realized vol ---------------------------------------------------------

def test_realized_vol_constant_series_is_zero():
    assert realized_vol([100.0] * 25, window=21) == 0.0


def test_realized_vol_varies_for_moving_series():
    v = realized_vol([100.0, 110.0, 100.0], window=2)
    assert v is not None and v > 0


def test_realized_vol_short_series_is_none():
    assert realized_vol([100.0, 101.0], window=21) is None


# ---- mean-reversion z-score ----------------------------------------------

def test_zscore_meanrev_above_mean_is_positive():
    z = zscore_meanrev([1.0, 2.0, 3.0, 4.0, 5.0], window=5)
    assert z is not None and abs(z - 1.41421356) < 1e-6


def test_zscore_meanrev_flat_window_is_zero():
    assert zscore_meanrev([5.0] * 5, window=5) == 0.0


def test_zscore_meanrev_short_series_is_none():
    assert zscore_meanrev([1.0, 2.0], window=5) is None


# ---- RSI ------------------------------------------------------------------

def test_rsi_all_up_is_100():
    assert rsi([float(i) for i in range(1, 17)], period=14) == 100.0


def test_rsi_all_down_is_zero():
    assert rsi([float(i) for i in range(16, 0, -1)], period=14) == 0.0


def test_rsi_mixed_is_between_bounds():
    closes = [10.0, 11.0, 10.5, 11.5, 11.0, 12.0, 11.5, 12.5, 12.0, 13.0,
              12.5, 13.5, 13.0, 14.0, 13.5]
    r = rsi(closes, period=14)
    assert r is not None and 0.0 < r < 100.0


def test_rsi_short_series_is_none():
    assert rsi([1.0, 2.0, 3.0], period=14) is None


# ---- cross-sectional helpers ---------------------------------------------

def test_winsorize_clamps_to_quantiles():
    assert winsorize([1.0, 2.0, 3.0, 4.0, 5.0], lo=0.25, hi=0.75) == [2.0, 2.0, 3.0, 4.0, 4.0]


def test_winsorize_preserves_none_positions():
    out = winsorize([1.0, None, 5.0])
    assert out[1] is None
    assert out[0] == 1.0 and out[2] == 5.0


def test_zscore_standardizes_cross_section():
    out = zscore([1.0, 2.0, 3.0])
    assert out[1] == 0.0
    assert abs(out[0] + 1.22474487) < 1e-6
    assert abs(out[2] - 1.22474487) < 1e-6


def test_zscore_all_equal_is_zeroes():
    assert zscore([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


def test_zscore_preserves_none():
    out = zscore([1.0, None, 3.0])
    assert out == [-1.0, None, 1.0]
