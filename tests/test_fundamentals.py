"""Tests for fundamentals ingestion (slice 25) — hermetic via an injected fetcher."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from traders.db import apply_migrations
from traders.fundamentals import (
    Fundamentals,
    fundamentals_from_info,
    ingest_fundamentals,
    latest_fundamentals,
    load_fundamentals,
    save_fundamentals,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


# A canned yfinance-style .info payload. The earnings epoch is built from a
# target date so the epoch->date conversion is checked as a round-trip.
_EARN_DATE = "2026-10-25"
_EARN_EPOCH = int(datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp())
_AAPL_INFO = {
    "currency": "USD",
    "marketCap": 3.0e12,
    "trailingEps": 6.5,
    "bookValue": 4.2,
    "freeCashflow": 1.0e11,
    "sharesOutstanding": 1.5e10,
    "earningsTimestamp": _EARN_EPOCH,
}


def _fetcher(table: dict[str, dict]):
    def fetch(ticker: str) -> dict:
        return table.get(ticker, {})

    return fetch


# ---- info -> row mapping ---------------------------------------------------


def test_fundamentals_from_info_maps_fields():
    f = fundamentals_from_info("AAPL", _AAPL_INFO, as_of="2026-06-01")
    assert f is not None
    assert f.ticker == "AAPL"
    assert f.as_of == "2026-06-01"
    assert f.currency == "USD"
    assert f.market_cap == 3.0e12
    assert f.trailing_eps == 6.5
    assert f.book_value_per_share == 4.2
    assert f.free_cash_flow == 1.0e11
    assert f.shares_outstanding == 1.5e10
    assert f.next_earnings_date == _EARN_DATE
    assert f.source == "yfinance"


def test_fundamentals_from_info_empty_is_none():
    assert fundamentals_from_info("AAPL", {}, as_of="2026-06-01") is None


def test_fundamentals_from_info_all_missing_numeric_is_none():
    # A payload with only non-numeric noise yields no usable fundamentals.
    info = {"currency": "USD", "longName": "Whatever Inc"}
    assert fundamentals_from_info("AAPL", info, as_of="2026-06-01") is None


def test_fundamentals_from_info_ignores_non_numeric_values():
    info = {"marketCap": "huge", "trailingEps": 2.0}
    f = fundamentals_from_info("AAA", info, as_of="2026-06-01")
    assert f is not None
    assert f.market_cap is None  # "huge" is not numeric -> dropped
    assert f.trailing_eps == 2.0


# ---- store round-trip ------------------------------------------------------


def test_save_and_load_round_trip():
    conn = _conn()
    row = fundamentals_from_info("AAPL", _AAPL_INFO, as_of="2026-06-01")
    assert save_fundamentals(conn, [row]) == 1
    loaded = load_fundamentals(conn, ticker="AAPL")
    assert len(loaded) == 1
    assert loaded[0] == row


def test_save_is_idempotent_on_key():
    conn = _conn()
    row = fundamentals_from_info("AAPL", _AAPL_INFO, as_of="2026-06-01")
    save_fundamentals(conn, [row])
    save_fundamentals(conn, [row])  # same (ticker, as_of, source) -> replace
    assert len(load_fundamentals(conn, ticker="AAPL")) == 1


def test_latest_fundamentals_is_lookahead_safe():
    conn = _conn()
    save_fundamentals(
        conn,
        [
            Fundamentals("AAPL", "2026-03-01", "USD", 1.0, None, None, None, None, None),
            Fundamentals("AAPL", "2026-06-01", "USD", 2.0, None, None, None, None, None),
        ],
    )
    # As of a date between the two snapshots, only the earlier one is visible.
    mid = latest_fundamentals(conn, "AAPL", as_of="2026-04-01")
    assert mid is not None and mid.as_of == "2026-03-01"
    # As of later, the newer snapshot wins.
    latest = latest_fundamentals(conn, "AAPL", as_of="2026-09-01")
    assert latest is not None and latest.as_of == "2026-06-01"
    # Before any snapshot, nothing is visible.
    assert latest_fundamentals(conn, "AAPL", as_of="2026-01-01") is None


# ---- ingest orchestration --------------------------------------------------


def test_ingest_writes_and_skips():
    conn = _conn()
    fetch = _fetcher({"AAPL": _AAPL_INFO, "EMPTY": {}})
    result = ingest_fundamentals(conn, ["AAPL", "EMPTY"], fetch_fn=fetch, as_of="2026-06-01")
    assert result["written"] == ["AAPL"]
    assert result["skipped"] == ["EMPTY"]
    assert len(load_fundamentals(conn, ticker="AAPL")) == 1


def test_ingest_skips_on_fetch_error_not_fatal():
    conn = _conn()

    def boom(ticker: str) -> dict:
        if ticker == "BAD":
            raise RuntimeError("network down")
        return _AAPL_INFO

    result = ingest_fundamentals(conn, ["BAD", "AAPL"], fetch_fn=boom, as_of="2026-06-01")
    assert result["written"] == ["AAPL"]
    assert result["skipped"] == ["BAD"]


def test_ingest_defaults_as_of_to_today():
    conn = _conn()
    result = ingest_fundamentals(conn, ["AAPL"], fetch_fn=_fetcher({"AAPL": _AAPL_INFO}))
    assert result["written"] == ["AAPL"]
    row = load_fundamentals(conn, ticker="AAPL")[0]
    assert len(row.as_of) == 10 and row.as_of.count("-") == 2  # an ISO date
