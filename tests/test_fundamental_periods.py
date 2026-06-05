"""Tests for period-by-period fundamentals (slice 33) — hermetic via injected fetcher."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from traders.db import apply_migrations
from traders.fundamental_periods import (
    availability_date,
    ingest_fundamental_periods,
    latest_periods,
    load_periods,
    load_periods_asof,
    periods_from_statements,
    save_periods,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def _period(period_end, period_type="annual", **over):
    """A normalized per-period statement dict (what the injected fetcher returns)."""
    base = {
        "period_end": period_end,
        "period_type": period_type,
        "currency": "USD",
        "revenue": 1000.0,
        "gross_profit": 400.0,
        "net_income": 100.0,
        "operating_cash_flow": 150.0,
        "capital_expenditure": -50.0,
        "total_assets": 2000.0,
        "current_assets": 800.0,
        "current_liabilities": 400.0,
        "long_term_debt": 300.0,
        "total_equity": 1200.0,
        "shares_outstanding": 1000.0,
    }
    base.update(over)
    return base


_AAA_STATEMENTS = [
    _period("2024-12-31", available_at="2025-02-15"),
    _period("2023-12-31", available_at="2024-02-15"),
]


def _fetcher(table: dict[str, list[dict]]):
    def fetch(ticker: str) -> list[dict]:
        return list(table.get(ticker, []))

    return fetch


# ---- statements -> rows mapping -------------------------------------------


def test_periods_from_statements_maps_fields():
    rows = periods_from_statements("AAA", [_period("2024-12-31", available_at="2025-02-15")])
    assert len(rows) == 1
    r = rows[0]
    assert r.ticker == "AAA"
    assert r.period_end == "2024-12-31"
    assert r.period_type == "annual"
    assert r.currency == "USD"
    assert r.revenue == 1000.0
    assert r.net_income == 100.0
    assert r.operating_cash_flow == 150.0
    assert r.capital_expenditure == -50.0
    assert r.total_equity == 1200.0
    assert r.available_at == "2025-02-15"
    assert r.source == "yfinance"


def test_periods_from_statements_coerces_and_drops_non_numeric():
    rows = periods_from_statements("AAA", [_period("2024-12-31", revenue="lots", net_income=5.0)])
    assert rows[0].revenue is None  # "lots" is not numeric -> dropped
    assert rows[0].net_income == 5.0


def test_periods_from_statements_drops_period_without_usable_numbers():
    bare = {"period_end": "2024-12-31", "period_type": "annual", "currency": "USD"}
    assert periods_from_statements("AAA", [bare]) == []


def test_periods_from_statements_drops_bad_period_end():
    assert periods_from_statements("AAA", [_period("not-a-date")]) == []
    assert periods_from_statements("AAA", [_period(None)]) == []


def test_periods_from_statements_drops_unknown_period_type():
    assert periods_from_statements("AAA", [_period("2024-12-31", period_type="ttm")]) == []


# ---- store round-trip ------------------------------------------------------


def test_save_and_load_round_trip():
    conn = _conn()
    rows = periods_from_statements("AAA", _AAA_STATEMENTS)
    assert save_periods(conn, rows) == 2
    loaded = load_periods(conn, ticker="AAA")
    assert len(loaded) == 2
    assert [r.period_end for r in loaded] == ["2024-12-31", "2023-12-31"]  # newest first
    assert loaded[0] == rows[0]


def test_save_is_idempotent_on_key():
    conn = _conn()
    rows = periods_from_statements("AAA", _AAA_STATEMENTS)
    save_periods(conn, rows)
    save_periods(conn, rows)  # same (ticker, period_end, period_type, source) -> replace
    assert len(load_periods(conn, ticker="AAA")) == 2


def test_annual_and_quarterly_same_period_end_coexist():
    conn = _conn()
    save_periods(
        conn,
        periods_from_statements(
            "AAA",
            [
                _period("2024-12-31", period_type="annual"),
                _period("2024-12-31", period_type="quarterly"),
            ],
        ),
    )
    assert len(load_periods(conn, ticker="AAA")) == 2
    assert len(load_periods(conn, ticker="AAA", period_type="quarterly")) == 1


# ---- look-ahead safety -----------------------------------------------------


def test_availability_date_prefers_filing_date():
    p = periods_from_statements("AAA", [_period("2024-12-31", available_at="2025-02-15")])[0]
    assert availability_date(p) == "2025-02-15"


def test_availability_date_falls_back_to_reporting_lag():
    annual = periods_from_statements("AAA", [_period("2024-12-31")])[0]
    assert availability_date(annual, annual_lag_days=90) == "2025-03-31"
    quarterly = periods_from_statements("AAA", [_period("2024-12-31", period_type="quarterly")])[0]
    assert availability_date(quarterly, quarterly_lag_days=45) == "2025-02-14"


def test_load_periods_asof_hides_unfiled_period():
    conn = _conn()
    save_periods(conn, periods_from_statements("AAA", _AAA_STATEMENTS))
    # Filed 2025-02-15; as of 2025-01-31 the 2024 period isn't public yet.
    visible = load_periods_asof(conn, as_of="2025-01-31", ticker="AAA")
    assert [r.period_end for r in visible] == ["2023-12-31"]
    # After the filing date, both are visible.
    later = load_periods_asof(conn, as_of="2025-03-01", ticker="AAA")
    assert [r.period_end for r in later] == ["2024-12-31", "2023-12-31"]


def test_load_periods_asof_uses_lag_when_no_filing_date():
    conn = _conn()
    save_periods(conn, periods_from_statements("AAA", [_period("2024-12-31")]))  # no available_at
    # period_end + 90d annual lag = 2025-03-31; not visible the day before.
    assert load_periods_asof(conn, as_of="2025-03-30", ticker="AAA") == []
    assert len(load_periods_asof(conn, as_of="2025-03-31", ticker="AAA")) == 1


def test_latest_periods_returns_newest_first_with_limit():
    conn = _conn()
    save_periods(
        conn,
        periods_from_statements(
            "AAA",
            [
                _period("2024-12-31", available_at="2025-02-15"),
                _period("2023-12-31", available_at="2024-02-15"),
                _period("2022-12-31", available_at="2023-02-15"),
            ],
        ),
    )
    rows = latest_periods(conn, "AAA", as_of="2025-06-01", limit=2)
    assert [r.period_end for r in rows] == ["2024-12-31", "2023-12-31"]


# ---- ingest orchestration --------------------------------------------------


def test_ingest_writes_and_skips():
    conn = _conn()
    fetch = _fetcher({"AAA": _AAA_STATEMENTS, "EMPTY": []})
    result = ingest_fundamental_periods(conn, ["AAA", "EMPTY"], fetch_fn=fetch)
    assert result["written"] == ["AAA"]
    assert result["skipped"] == ["EMPTY"]
    assert result["periods"] == 2
    assert len(load_periods(conn, ticker="AAA")) == 2


def test_ingest_skips_on_fetch_error_not_fatal():
    conn = _conn()

    def boom(ticker: str) -> list[dict]:
        if ticker == "BAD":
            raise RuntimeError("network down")
        return _AAA_STATEMENTS

    result = ingest_fundamental_periods(conn, ["BAD", "AAA"], fetch_fn=boom)
    assert result["written"] == ["AAA"]
    assert result["skipped"] == ["BAD"]
