"""Tests for thesis-intact monitoring (slice 43 / B6) — hermetic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from traders.db import apply_migrations
from traders.fundamental_periods import FundamentalPeriod, save_periods
from traders.intact import thesis_intact

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def _fp(period_end: str, *, available_at=None, **over) -> FundamentalPeriod:
    base = dict(
        ticker="AAA",
        period_end=period_end,
        period_type="annual",
        currency="USD",
        revenue=1000.0,
        gross_profit=400.0,
        net_income=100.0,
        operating_cash_flow=150.0,
        capital_expenditure=-30.0,
        total_assets=2000.0,
        current_assets=800.0,
        current_liabilities=400.0,
        long_term_debt=300.0,
        total_equity=1000.0,
        shares_outstanding=1000.0,
        available_at=available_at,
        source="yfinance",
    )
    base.update(over)
    return FundamentalPeriod(**base)


def _healthy_pair() -> list[FundamentalPeriod]:
    # Improving, profitable, cash-generative, liquid -> nothing should fire.
    return [
        _fp(
            "2024-12-31",
            available_at="2025-02-15",
            net_income=120.0,
            gross_profit=420.0,
            revenue=1200.0,
        ),
        _fp(
            "2023-12-31",
            available_at="2024-02-15",
            net_income=100.0,
            gross_profit=300.0,
            revenue=1000.0,
        ),
    ]


def test_healthy_business_is_intact():
    conn = _conn()
    save_periods(conn, _healthy_pair())
    report = thesis_intact(conn, "AAA", as_of="2025-06-01")
    assert report.checked is True
    assert report.intact is True
    assert report.warnings == []


def test_no_statements_is_unchecked_not_a_false_all_clear():
    conn = _conn()
    report = thesis_intact(conn, "AAA", as_of="2025-06-01")
    assert report.checked is False
    assert report.intact is True  # silence, not a claim
    assert report.warnings == []


def test_lossmaking_flags_broken_premise():
    conn = _conn()
    save_periods(conn, [_fp("2024-12-31", available_at="2025-02-15", net_income=-10.0)])
    report = thesis_intact(conn, "AAA", as_of="2025-06-01")
    assert report.intact is False
    assert any("lossmaking" in w for w in report.warnings)


def test_cash_burn_flags():
    conn = _conn()
    save_periods(
        conn,
        [
            _fp(
                "2024-12-31",
                available_at="2025-02-15",
                operating_cash_flow=20.0,
                capital_expenditure=-50.0,
            )
        ],  # owner earnings -30
    )
    report = thesis_intact(conn, "AAA", as_of="2025-06-01")
    assert any("Burning cash" in w for w in report.warnings)


def test_tight_liquidity_flags():
    conn = _conn()
    save_periods(
        conn,
        [
            _fp(
                "2024-12-31",
                available_at="2025-02-15",
                current_assets=300.0,
                current_liabilities=500.0,
            )
        ],
    )
    report = thesis_intact(conn, "AAA", as_of="2025-06-01")
    assert any("Liquidity is tight" in w for w in report.warnings)


def test_weak_quality_flags():
    conn = _conn()
    # cur worse than prev across the board -> low Piotroski.
    save_periods(
        conn,
        [
            _fp(
                "2024-12-31",
                available_at="2025-02-15",
                net_income=10.0,
                gross_profit=270.0,
                revenue=900.0,
                operating_cash_flow=20.0,
                total_assets=2200.0,
                current_assets=440.0,
                long_term_debt=600.0,
                shares_outstanding=1200.0,
            ),
            _fp(
                "2023-12-31",
                available_at="2024-02-15",
                net_income=100.0,
                gross_profit=400.0,
                revenue=1000.0,
                operating_cash_flow=120.0,
                total_assets=2000.0,
                current_assets=800.0,
                long_term_debt=200.0,
                shares_outstanding=1000.0,
            ),
        ],
    )
    report = thesis_intact(conn, "AAA", as_of="2025-06-01")
    assert any("Piotroski" in w for w in report.warnings)


def test_margin_collapse_flags():
    conn = _conn()
    save_periods(
        conn,
        [
            _fp("2024-12-31", available_at="2025-02-15", gross_profit=240.0, revenue=1200.0),  # 20%
            _fp("2023-12-31", available_at="2024-02-15", gross_profit=350.0, revenue=1000.0),  # 35%
        ],
    )
    report = thesis_intact(conn, "AAA", as_of="2025-06-01")
    assert any("Gross margin is shrinking" in w for w in report.warnings)


def test_intact_is_lookahead_safe():
    conn = _conn()
    save_periods(conn, [_fp("2024-12-31", available_at="2025-02-15", net_income=-10.0)])
    # Before the filing date, the lossmaking period isn't visible -> unchecked.
    early = thesis_intact(conn, "AAA", as_of="2025-01-01")
    assert early.checked is False
    assert thesis_intact(conn, "AAA", as_of="2025-06-01").checked is True
