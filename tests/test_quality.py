"""Tests for the Piotroski F-score quality screen (slice 34) — pure + hermetic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from traders.db import apply_migrations
from traders.fundamental_periods import FundamentalPeriod, save_periods
from traders.quality import (
    PiotroskiScore,
    piotroski_components,
    piotroski_for,
    piotroski_score,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def _fp(
    period_end: str, *, period_type: str = "annual", available_at=None, **over
) -> FundamentalPeriod:
    base = dict(
        ticker="AAA",
        period_end=period_end,
        period_type=period_type,
        currency="USD",
        revenue=1000.0,
        gross_profit=300.0,
        net_income=50.0,
        operating_cash_flow=60.0,
        capital_expenditure=-20.0,
        total_assets=2000.0,
        current_assets=600.0,
        current_liabilities=400.0,
        long_term_debt=500.0,
        total_equity=800.0,
        shares_outstanding=1000.0,
        available_at=available_at,
        source="yfinance",
    )
    base.update(over)
    return FundamentalPeriod(**base)


# A year-over-year pair where every one of the nine tests improves -> 9/9.
_PREV = _fp(
    "2023-12-31",
    revenue=1000.0,
    gross_profit=300.0,  # 30% margin
    net_income=50.0,
    operating_cash_flow=60.0,
    total_assets=2000.0,
    current_assets=600.0,
    current_liabilities=400.0,  # current ratio 1.5
    long_term_debt=500.0,  # leverage 0.25
    shares_outstanding=1000.0,
)
_CUR = _fp(
    "2024-12-31",
    revenue=1200.0,
    gross_profit=420.0,  # 35% margin (up)
    net_income=120.0,
    operating_cash_flow=150.0,  # > net income (good accruals)
    total_assets=2100.0,
    current_assets=800.0,
    current_liabilities=400.0,  # current ratio 2.0 (up)
    long_term_debt=300.0,  # leverage 0.14 (down)
    shares_outstanding=1000.0,  # no dilution
)


# ---- composite score -------------------------------------------------------


def test_perfect_pair_scores_nine():
    result = piotroski_score(_CUR, _PREV)
    assert isinstance(result, PiotroskiScore)
    assert result.score == 9
    assert result.computable == 9
    assert all(v == 1 for v in result.components.values())


def test_worst_pair_scores_zero():
    prev = _fp(
        "2023-12-31",
        revenue=1000.0,
        gross_profit=400.0,  # 40% margin
        net_income=100.0,
        operating_cash_flow=120.0,
        total_assets=2000.0,
        current_assets=800.0,
        current_liabilities=400.0,  # current ratio 2.0
        long_term_debt=200.0,  # leverage 0.10
        shares_outstanding=1000.0,
    )
    cur = _fp(
        "2024-12-31",
        revenue=900.0,
        gross_profit=270.0,  # 30% margin (down)
        net_income=-10.0,  # ROA negative
        operating_cash_flow=-20.0,  # CFO negative, and < net income
        total_assets=2200.0,
        current_assets=440.0,
        current_liabilities=400.0,  # current ratio 1.1 (down)
        long_term_debt=600.0,  # leverage 0.27 (up)
        shares_outstanding=1200.0,  # dilution
    )
    result = piotroski_score(cur, prev)
    assert result.score == 0
    assert result.computable == 9


def test_components_are_correct():
    comps = piotroski_components(_CUR, _PREV)
    assert comps["roa_positive"] == 1
    assert comps["cfo_positive"] == 1
    assert comps["roa_improving"] == 1
    assert comps["accruals"] == 1  # CFO 150 > net income 120
    assert comps["leverage_decreasing"] == 1
    assert comps["current_ratio_improving"] == 1
    assert comps["no_dilution"] == 1
    assert comps["gross_margin_improving"] == 1
    assert comps["asset_turnover_improving"] == 1


def test_missing_fields_reduce_computable_not_score():
    # Drop the two fields gross margin and dilution need -> those tests become
    # non-computable; the remaining seven still pass.
    cur = _fp(
        "2024-12-31", **{**_field_overrides(_CUR), "gross_profit": None, "shares_outstanding": None}
    )
    prev = _fp("2023-12-31", **{**_field_overrides(_PREV), "gross_profit": None})
    result = piotroski_score(cur, prev)
    assert result.components["gross_margin_improving"] is None
    assert result.components["no_dilution"] is None
    assert result.computable == 7
    assert result.score == 7


def test_missing_total_assets_drops_assets_based_tests():
    cur = _fp("2024-12-31", **{**_field_overrides(_CUR), "total_assets": None})
    result = piotroski_score(cur, _PREV)
    # ROA, ROA-improving, leverage, and asset-turnover all need total assets.
    for name in (
        "roa_positive",
        "roa_improving",
        "leverage_decreasing",
        "asset_turnover_improving",
    ):
        assert result.components[name] is None
    assert result.computable == 5


# ---- look-ahead-safe accessor ---------------------------------------------


def test_piotroski_for_uses_latest_available_annual_pair():
    conn = _conn()
    save_periods(
        conn,
        [
            _fp("2022-12-31", available_at="2023-02-15", **_field_overrides(_PREV)),
            _fp("2023-12-31", available_at="2024-02-15", **_field_overrides(_PREV)),
            _fp("2024-12-31", available_at="2025-02-15", **_field_overrides(_CUR)),
            # A quarterly row with the same latest period_end must be ignored.
            _fp("2024-12-31", period_type="quarterly", available_at="2025-02-15"),
        ],
    )
    result = piotroski_for(conn, "AAA", as_of="2025-06-01")
    assert result == piotroski_score(_CUR, _PREV)  # 2024 vs 2023 annual, quarterly ignored


def test_piotroski_for_is_lookahead_safe():
    conn = _conn()
    save_periods(
        conn,
        [
            _fp("2022-12-31", available_at="2023-02-15", **_field_overrides(_PREV)),
            _fp("2023-12-31", available_at="2024-02-15", **_field_overrides(_PREV)),
            _fp("2024-12-31", available_at="2025-02-15", **_field_overrides(_CUR)),
        ],
    )
    # Before the 2023 filing only the 2022 period is public -> < 2 -> None.
    assert piotroski_for(conn, "AAA", as_of="2023-06-01") is None
    # After the 2024 filing, two periods are public -> a score.
    assert piotroski_for(conn, "AAA", as_of="2025-06-01") is not None


def test_piotroski_for_needs_two_periods():
    conn = _conn()
    save_periods(conn, [_fp("2024-12-31", available_at="2025-02-15", **_field_overrides(_CUR))])
    assert piotroski_for(conn, "AAA", as_of="2025-06-01") is None


def _field_overrides(p: FundamentalPeriod) -> dict:
    """The numeric/currency fields of a period, for rebuilding a variant of it."""
    return dict(
        revenue=p.revenue,
        gross_profit=p.gross_profit,
        net_income=p.net_income,
        operating_cash_flow=p.operating_cash_flow,
        capital_expenditure=p.capital_expenditure,
        total_assets=p.total_assets,
        current_assets=p.current_assets,
        current_liabilities=p.current_liabilities,
        long_term_debt=p.long_term_debt,
        total_equity=p.total_equity,
        shares_outstanding=p.shares_outstanding,
    )
