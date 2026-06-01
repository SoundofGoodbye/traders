"""Tests for trial-aware Sharpe deflation (PSR / DSR) — slice 24.

Hand-checkable fixtures plus the two properties the gate relies on: more trades
raise confidence, and more trials (the multiple-testing penalty) lower it.
"""

from __future__ import annotations

import math

from traders.deflated_sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    sharpe_estimator_variance,
)

# ---- probabilistic Sharpe ratio -------------------------------------------


def test_psr_at_benchmark_is_one_half():
    # Φ(0) = 0.5: the observed Sharpe sits exactly on the benchmark.
    assert probabilistic_sharpe_ratio(0.5, 30, benchmark=0.5) == 0.5


def test_psr_above_benchmark_exceeds_half():
    assert probabilistic_sharpe_ratio(0.4, 30, benchmark=0.0) > 0.5


def test_psr_below_benchmark_under_half():
    assert probabilistic_sharpe_ratio(-0.4, 30, benchmark=0.0) < 0.5


def test_psr_more_trades_more_confident():
    few = probabilistic_sharpe_ratio(0.4, 5)
    many = probabilistic_sharpe_ratio(0.4, 100)
    assert few is not None and many is not None
    assert many > few  # same edge, longer record -> more confident


def test_psr_fat_tails_lower_confidence():
    thin = probabilistic_sharpe_ratio(0.4, 30, kurtosis=3.0)
    fat = probabilistic_sharpe_ratio(0.4, 30, kurtosis=9.0)
    assert thin is not None and fat is not None
    assert fat < thin


def test_psr_none_below_two_trades():
    assert probabilistic_sharpe_ratio(0.4, 1) is None


# ---- expected maximum Sharpe (the multiple-testing benchmark) -------------


def test_expected_max_one_trial_is_zero():
    # A single trial has no selection bias -> nothing to deflate against.
    assert expected_max_sharpe(1, 1.0) == 0.0


def test_expected_max_rises_with_trials():
    assert expected_max_sharpe(50, 1.0) > expected_max_sharpe(2, 1.0) > 0.0


def test_expected_max_two_trials_known_value():
    # sqrt(V)=1: (1-γ)·Z(0.5) + γ·Z(1 - 1/(2e)) = γ·Z(0.81606) ≈ 0.5772·0.9004.
    assert math.isclose(expected_max_sharpe(2, 1.0), 0.5197, abs_tol=2e-3)


# ---- estimator variance ----------------------------------------------------


def test_estimator_variance_shrinks_with_n():
    assert sharpe_estimator_variance(0.5, 100) < sharpe_estimator_variance(0.5, 10)


def test_estimator_variance_inf_below_two():
    assert sharpe_estimator_variance(0.5, 1) == float("inf")


# ---- deflated Sharpe ratio -------------------------------------------------


def _weak_edge(n: int) -> list[float]:
    # Alternating +2 / -1: mean 0.5, pstdev 1.5 -> Sharpe ~0.33 (a soft edge).
    return [2.0 if i % 2 == 0 else -1.0 for i in range(n)]


def _strong_edge(n: int) -> list[float]:
    # Alternating +5 / +1: mean 3.0, pstdev 2.0 -> Sharpe 1.5 (a clear edge).
    return [5.0 if i % 2 == 0 else 1.0 for i in range(n)]


def test_dsr_more_trials_lower_confidence():
    pnls = _weak_edge(40)
    one = deflated_sharpe_ratio(pnls, 1)
    many = deflated_sharpe_ratio(pnls, 200)
    assert one is not None and many is not None
    assert many < one  # anti-fishing: more trials -> harder to clear


def test_dsr_strong_edge_high_confidence_single_trial():
    dsr = deflated_sharpe_ratio(_strong_edge(60), 1)
    assert dsr is not None and dsr > 0.9


def test_dsr_none_for_too_few_trades():
    assert deflated_sharpe_ratio([1.0], 1) is None


def test_dsr_none_for_zero_dispersion():
    assert deflated_sharpe_ratio([2.0, 2.0, 2.0], 1) is None
