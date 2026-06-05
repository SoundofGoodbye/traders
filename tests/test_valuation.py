"""Tests for the intrinsic-value / margin-of-safety estimate (slice 36 / B3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from traders.db import apply_migrations
from traders.fundamental_periods import periods_from_statements, save_periods
from traders.valuation import (
    IntrinsicValue,
    intrinsic_value,
    intrinsic_value_for,
    normalized_owner_earnings,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def _annual(period_end: str, *, available_at=None, **fig) -> dict:
    return {
        "period_end": period_end,
        "period_type": "annual",
        "available_at": available_at,
        **fig,
    }


def _periods(*specs) -> list:
    return periods_from_statements("AAA", list(specs))


# ---- normalized owner earnings --------------------------------------------


def test_normalized_owner_earnings_averages_cfo_plus_capex():
    ps = _periods(
        _annual("2024-12-31", operating_cash_flow=1200.0, capital_expenditure=-200.0),
        _annual("2023-12-31", operating_cash_flow=1000.0, capital_expenditure=-100.0),
    )
    norm = normalized_owner_earnings(ps)
    assert norm is not None
    mean, years = norm
    assert years == 2
    assert mean == pytest.approx((1000.0 + 900.0) / 2)  # (1200-200), (1000-100)


def test_normalized_requires_min_periods():
    ps = _periods(_annual("2024-12-31", operating_cash_flow=1000.0, capital_expenditure=-100.0))
    assert normalized_owner_earnings(ps, min_periods=2) is None
    assert normalized_owner_earnings(ps, min_periods=1) is not None


def test_normalized_skips_periods_missing_cashflow():
    ps = _periods(
        _annual("2024-12-31", operating_cash_flow=1000.0, capital_expenditure=-100.0),
        _annual("2023-12-31", net_income=50.0),  # no CFO/capex -> not usable
    )
    assert normalized_owner_earnings(ps) is None  # only one usable < min 2


# ---- intrinsic value -------------------------------------------------------


def test_intrinsic_value_math():
    ps = _periods(
        _annual(
            "2024-12-31",
            operating_cash_flow=11500.0,
            capital_expenditure=-300.0,
            shares_outstanding=1000.0,
        ),
        _annual(
            "2023-12-31",
            operating_cash_flow=11000.0,
            capital_expenditure=-300.0,
            shares_outstanding=1000.0,
        ),
    )
    iv = intrinsic_value(ps, price=100.0)
    assert iv is not None
    oe = (11200.0 + 10700.0) / 2  # 10950
    multiple = 1.02 / 0.08  # (1+g)/(r-g), r=10% g=2%
    assert iv.owner_earnings == pytest.approx(oe)
    assert iv.years == 2
    assert iv.iv_total == pytest.approx(oe * multiple)
    assert iv.iv_per_share == pytest.approx(oe * multiple / 1000.0)
    assert iv.margin_of_safety == pytest.approx((iv.iv_per_share - 100.0) / iv.iv_per_share)
    market_value = 100.0 * 1000.0
    assert iv.implied_growth == pytest.approx((market_value * 0.10 - oe) / (oe + market_value))


def test_intrinsic_value_none_for_negative_owner_earnings():
    ps = _periods(
        _annual(
            "2024-12-31",
            operating_cash_flow=100.0,
            capital_expenditure=-300.0,
            shares_outstanding=1000.0,
        ),
        _annual(
            "2023-12-31",
            operating_cash_flow=120.0,
            capital_expenditure=-300.0,
            shares_outstanding=1000.0,
        ),
    )
    assert intrinsic_value(ps, price=100.0) is None  # owner earnings <= 0


def test_intrinsic_value_none_without_usable_price():
    ps = _periods(
        _annual(
            "2024-12-31",
            operating_cash_flow=1000.0,
            capital_expenditure=-100.0,
            shares_outstanding=1000.0,
        ),
        _annual(
            "2023-12-31",
            operating_cash_flow=1000.0,
            capital_expenditure=-100.0,
            shares_outstanding=1000.0,
        ),
    )
    assert intrinsic_value(ps, price=0.0) is None
    assert intrinsic_value(ps, price=None) is None


def test_intrinsic_value_none_without_shares():
    ps = _periods(
        _annual("2024-12-31", operating_cash_flow=1000.0, capital_expenditure=-100.0),
        _annual("2023-12-31", operating_cash_flow=1000.0, capital_expenditure=-100.0),
    )
    assert intrinsic_value(ps, price=100.0) is None  # no shares to per-share


def test_returns_intrinsic_value_dataclass():
    ps = _periods(
        _annual(
            "2024-12-31",
            operating_cash_flow=11500.0,
            capital_expenditure=-300.0,
            shares_outstanding=1000.0,
        ),
        _annual(
            "2023-12-31",
            operating_cash_flow=11000.0,
            capital_expenditure=-300.0,
            shares_outstanding=1000.0,
        ),
    )
    assert isinstance(intrinsic_value(ps, price=100.0), IntrinsicValue)


# ---- look-ahead-safe accessor ---------------------------------------------


def test_intrinsic_value_for_is_lookahead_safe():
    conn = _conn()
    save_periods(
        conn,
        periods_from_statements(
            "AAA",
            [
                _annual(
                    "2024-12-31",
                    available_at="2025-02-15",
                    operating_cash_flow=11500.0,
                    capital_expenditure=-300.0,
                    shares_outstanding=1000.0,
                ),
                _annual(
                    "2023-12-31",
                    available_at="2024-02-15",
                    operating_cash_flow=11000.0,
                    capital_expenditure=-300.0,
                    shares_outstanding=1000.0,
                ),
            ],
        ),
    )
    # Before the 2024 filing only one period is public -> below min -> None.
    assert intrinsic_value_for(conn, "AAA", as_of="2025-01-01", price=100.0) is None
    iv = intrinsic_value_for(conn, "AAA", as_of="2025-06-01", price=100.0)
    assert iv is not None and iv.years == 2
