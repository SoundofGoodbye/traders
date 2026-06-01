"""Tests for the Tiingo price source (slice 30) — hermetic, no network/token."""

from __future__ import annotations

import pytest

from traders.tiingo import _default_tiingo_fetcher, parse_tiingo_csv, to_tiingo_symbol

# Tiingo daily CSV: adjClose differs from close so the test proves we use the
# split/dividend-ADJUSTED column. Row 3 has an empty adjClose and must drop;
# row 4 carries a trailing timestamp on the date.
SAMPLE_CSV = (
    "date,close,high,low,open,volume,adjClose,adjHigh,adjLow,adjOpen,adjVolume,divCash,splitFactor\n"
    "2026-01-02,190.0,191,189,190.5,1000,95.0,95.5,94.5,95.2,1000,0.0,1.0\n"
    "2026-01-03,192.0,193,191,191.5,1100,96.0,96.5,95.5,95.8,1100,0.0,1.0\n"
    "2026-01-04,193.0,194,192,193.5,1200,,,,,,0.0,1.0\n"
    "2026-01-05T00:00:00.000Z,195.0,196,194,194.5,1300,97.5,98,97,97.2,1300,0.0,1.0\n"
)


# ---- symbol map -----------------------------------------------------------


def test_us_ticker_is_passed_through_uppercased():
    assert to_tiingo_symbol("aapl") == "AAPL"
    assert to_tiingo_symbol("MSFT") == "MSFT"


def test_us_class_share_uses_dash():
    assert to_tiingo_symbol("BRK.B") == "BRK-B"


def test_foreign_suffixes_and_empty_are_none():
    # Tiingo's free tier is US EOD — skip foreign venues rather than burn calls.
    assert to_tiingo_symbol("MC.PA") is None
    assert to_tiingo_symbol("ADS.DE") is None
    assert to_tiingo_symbol("") is None


# ---- CSV parsing ----------------------------------------------------------


def test_parse_uses_adjusted_close_and_drops_bad_rows():
    rows = parse_tiingo_csv(SAMPLE_CSV)
    assert rows == [
        ("2026-01-02", 95.0),
        ("2026-01-03", 96.0),
        ("2026-01-05", 97.5),  # timestamp truncated to the date; empty row dropped
    ]


def test_parse_empty_text():
    assert parse_tiingo_csv("") == []


# ---- default fetcher (token requirement) ----------------------------------


def test_fetcher_requires_token(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TIINGO_API_KEY"):
        _default_tiingo_fetcher()


def test_fetcher_builds_with_explicit_token():
    # Construction succeeds with a token; no network happens until it is called.
    fetch = _default_tiingo_fetcher(token="dummy-token")
    assert callable(fetch)


def test_fetcher_reads_token_from_env(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "env-token")
    assert callable(_default_tiingo_fetcher())
