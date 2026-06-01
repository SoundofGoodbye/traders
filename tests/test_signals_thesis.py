"""Tests for the signal-driven thesis generator (slice 20)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from traders import analyst, research, scout
from traders.db import apply_migrations
from traders.prices import PriceHistory, save_prices
from traders.signals_thesis import SignalThesisGenerator, build_signal_generator

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
START = date(2025, 1, 1)


def _series(closes: list[float], start: date = START) -> tuple[tuple[str, float], ...]:
    out: list[tuple[str, float]] = []
    day = start
    for c in closes:
        out.append((day.isoformat(), float(c)))
        day += timedelta(days=1)
    return tuple(out)


def _gen(ticker: str, closes: list[float], **kw) -> SignalThesisGenerator:
    hist = PriceHistory(series={ticker: _series(closes)})
    as_of = START + timedelta(days=len(closes))  # all closes are strictly before
    return SignalThesisGenerator(history=hist, as_of=as_of, **kw)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


# ---- generator behaviour --------------------------------------------------


def test_uptrend_yields_momentum_long():
    closes = [100.0 * (1.01**i) for i in range(260)]
    drafts = _gen("AAA", closes).generate("AAA", "")
    assert len(drafts) == 1
    d = drafts[0]
    assert d.thesis_type == "momentum"
    assert d.direction == "long"
    assert d.conviction == 5  # very strong 12-1 momentum
    assert 0.0 < d.suggested_size_pct <= 5.0


def test_oversold_decline_yields_mean_reversion_long():
    closes = [100.0 - i for i in range(60)]  # steady decline -> oversold
    drafts = _gen("AAA", closes).generate("AAA", "")
    assert len(drafts) == 1
    d = drafts[0]
    assert d.thesis_type == "mean-reversion"
    assert d.direction == "long"
    assert d.conviction in (3, 4)


def test_insufficient_history_yields_no_thesis():
    assert _gen("AAA", [100.0] * 30).generate("AAA", "") == []


def test_flat_series_yields_no_thesis():
    # Enough history, but no momentum and not oversold -> no actionable long.
    assert _gen("AAA", [100.0] * 100).generate("AAA", "") == []


def test_unknown_ticker_yields_no_thesis():
    assert _gen("AAA", [100.0] * 100).generate("ZZZ", "") == []


def test_low_vol_uptrend_sizes_at_base():
    # A clean 1%/day climb has ~zero return-dispersion -> base size.
    closes = [100.0 * (1.01**i) for i in range(260)]
    d = _gen("AAA", closes, base_size_pct=2.0).generate("AAA", "")[0]
    assert d.suggested_size_pct == 2.0


# ---- integration through the Analyst -------------------------------------


def test_analyst_persists_signal_thesis(tmp_path):
    conn = _conn()
    # Seed an uptrend in prices for AAA, all before the as_of date.
    closes = [100.0 * (1.01**i) for i in range(260)]
    save_prices(conn, "AAA", list(_series(closes)))

    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    scout.run(conn, watchlist_path=wl, batch_size=1)
    research.run(conn)  # stub note; the signal generator reads prices, not the note

    as_of = START + timedelta(days=len(closes))
    gen = build_signal_generator(conn, as_of=as_of)
    run_id, n = analyst.run(conn, generator=gen)

    assert n == 1
    row = conn.execute(
        "SELECT ticker, thesis_type, direction FROM theses WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row == ("AAA", "momentum", "long")


def test_build_signal_generator_empty_prices_is_graceful():
    conn = _conn()
    gen = build_signal_generator(conn, as_of=date(2026, 1, 1))
    assert gen.generate("AAA", "") == []  # no prices -> no thesis, no crash
