"""Tests for the historical price store + PriceHistory abstraction."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from traders.db import apply_migrations
from traders.prices import (
    PriceHistory,
    load_history_from_db,
    save_prices,
    synthetic_history,
    weekdays,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def _hist() -> PriceHistory:
    return PriceHistory(
        series={"AAA": (("2026-01-05", 100.0), ("2026-01-08", 110.0), ("2026-01-15", 90.0))}
    )


def test_close_asof_exact_day():
    assert _hist().close_asof("AAA", date(2026, 1, 8)) == 110.0


def test_close_asof_backfills_to_most_recent_prior():
    # No close on the 10th -> use the 8th.
    assert _hist().close_asof("AAA", date(2026, 1, 10)) == 110.0


def test_close_asof_before_first_is_none():
    assert _hist().close_asof("AAA", date(2026, 1, 1)) is None


def test_close_asof_unknown_ticker_is_none():
    assert _hist().close_asof("ZZZ", date(2026, 1, 8)) is None


def test_close_asof_after_last_uses_last():
    assert _hist().close_asof("AAA", date(2026, 2, 1)) == 90.0


def test_weekdays_excludes_weekends():
    # 2026-01-01 is a Thursday; the 3rd/4th are the weekend.
    days = weekdays(date(2026, 1, 1), date(2026, 1, 5))
    assert days == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5)]


def test_synthetic_is_deterministic():
    a = synthetic_history(["AAA", "BBB"], date(2026, 1, 1), date(2026, 1, 31))
    b = synthetic_history(["AAA", "BBB"], date(2026, 1, 1), date(2026, 1, 31))
    assert a == b


def test_synthetic_differs_per_ticker():
    h = synthetic_history(["AAA", "BBB"], date(2026, 1, 1), date(2026, 1, 31))
    assert h.series["AAA"] != h.series["BBB"]


def test_synthetic_one_close_per_weekday():
    start, end = date(2026, 1, 1), date(2026, 1, 31)
    h = synthetic_history(["AAA"], start, end)
    assert len(h.series["AAA"]) == len(weekdays(start, end))


def test_db_round_trip():
    conn = _conn()
    save_prices(conn, "AAA", [("2026-01-05", 100.0), ("2026-01-08", 110.0)])
    save_prices(conn, "BBB", [("2026-01-05", 50.0)])
    hist = load_history_from_db(conn)
    assert hist.close_asof("AAA", date(2026, 1, 8)) == 110.0
    assert hist.close_asof("BBB", date(2026, 1, 5)) == 50.0


def test_db_load_filters_by_ticker_and_window():
    conn = _conn()
    save_prices(conn, "AAA", [("2026-01-05", 100.0), ("2026-02-05", 120.0)])
    save_prices(conn, "BBB", [("2026-01-05", 50.0)])
    hist = load_history_from_db(
        conn, tickers=["AAA"], start=date(2026, 1, 1), end=date(2026, 1, 31)
    )
    assert hist.tickers() == ("AAA",)
    assert hist.close_asof("AAA", date(2026, 2, 28)) == 100.0  # Feb row filtered out


def test_save_prices_replaces_existing_day():
    conn = _conn()
    save_prices(conn, "AAA", [("2026-01-05", 100.0)])
    save_prices(conn, "AAA", [("2026-01-05", 105.0)])
    hist = load_history_from_db(conn)
    assert hist.close_asof("AAA", date(2026, 1, 5)) == 105.0
