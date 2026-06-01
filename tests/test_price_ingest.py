"""Tests for the Stooq price ingestor + symbol map (slice 19)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from traders.db import apply_migrations
from traders.price_ingest import ingest_prices, parse_stooq_csv
from traders.prices import load_history_from_db
from traders.stooq_symbols import to_stooq_symbol

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

SAMPLE_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-01-02,10,11,9,10.5,1000\n"
    "2026-01-03,10.5,12,10,11.5,2000\n"
    "2026-01-04,11.5,12,11,N/D,0\n"  # N/D close must be dropped
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


# ---- symbol map -----------------------------------------------------------

def test_us_ticker_maps_to_us_suffix():
    assert to_stooq_symbol("AAPL") == "aapl.us"


def test_us_class_share_maps_with_dash():
    assert to_stooq_symbol("BRK.B") == "brk-b.us"


def test_eu_suffixes_map_to_stooq_markets():
    assert to_stooq_symbol("MC.PA") == "mc.fr"
    assert to_stooq_symbol("ADS.DE") == "ads.de"
    assert to_stooq_symbol("ASML.AS") == "asml.nl"
    assert to_stooq_symbol("ENEL.MI") == "enel.it"
    assert to_stooq_symbol("BBVA.MC") == "bbva.es"
    assert to_stooq_symbol("FLTR.IR") == "fltr.ie"


def test_unknown_suffix_and_empty_are_none():
    assert to_stooq_symbol("FOO.ZZ") is None
    assert to_stooq_symbol("") is None


# ---- CSV parsing ----------------------------------------------------------

def test_parse_stooq_csv_drops_bad_rows():
    rows = parse_stooq_csv(SAMPLE_CSV)
    assert rows == [("2026-01-02", 10.5), ("2026-01-03", 11.5)]


def test_parse_stooq_csv_empty_text():
    assert parse_stooq_csv("") == []


# ---- ingestion ------------------------------------------------------------

def test_ingest_writes_under_original_ticker():
    conn = _conn()
    result = ingest_prices(conn, ["MC.PA"], fetch_csv=lambda _s: SAMPLE_CSV)
    assert result["written"] == {"MC.PA": 2}
    hist = load_history_from_db(conn)
    # Stored under the watchlist ticker, not the Stooq symbol.
    assert hist.tickers() == ("MC.PA",)
    assert hist.close_asof("MC.PA", date(2026, 1, 3)) == 11.5


def test_ingest_is_idempotent():
    conn = _conn()
    fetch = lambda _s: SAMPLE_CSV  # noqa: E731
    ingest_prices(conn, ["AAPL"], fetch_csv=fetch)
    ingest_prices(conn, ["AAPL"], fetch_csv=fetch)
    count = conn.execute("SELECT COUNT(*) FROM prices WHERE ticker = 'AAPL'").fetchone()[0]
    assert count == 2


def test_ingest_skips_unmapped_without_fetching():
    conn = _conn()
    calls: list[str] = []

    def fetch(symbol: str) -> str:
        calls.append(symbol)
        return SAMPLE_CSV

    result = ingest_prices(conn, ["FOO.ZZ"], fetch_csv=fetch)
    assert result["skipped"] == ["FOO.ZZ"]
    assert calls == []  # unmapped tickers never hit the fetcher


def test_ingest_skips_on_fetch_error():
    conn = _conn()

    def boom(_symbol: str) -> str:
        raise RuntimeError("network down")

    result = ingest_prices(conn, ["AAPL"], fetch_csv=boom)
    assert result["written"] == {}
    assert result["skipped"] == ["AAPL"]


def test_ingest_since_filters_rows():
    conn = _conn()
    result = ingest_prices(
        conn, ["AAPL"], fetch_csv=lambda _s: SAMPLE_CSV, since="2026-01-03"
    )
    assert result["written"] == {"AAPL": 1}
    hist = load_history_from_db(conn)
    assert hist.close_asof("AAPL", date(2026, 1, 2)) is None
    assert hist.close_asof("AAPL", date(2026, 1, 3)) == 11.5
