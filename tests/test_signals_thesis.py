"""Tests for the signal-driven thesis generator (slice 20)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from traders import analyst, research, scout
from traders.db import apply_migrations
from traders.fundamentals import Fundamentals, save_fundamentals
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


# ---- fundamental value / catalyst (slice 26) -----------------------------


def _cheap(ticker: str, next_earnings: str | None = None) -> Fundamentals:
    # vs a $100 price: E/P 8%, B/P 0.6, FCF yield 10% -> all three cheap flags.
    return Fundamentals(
        ticker=ticker,
        as_of="2025-01-01",
        currency="USD",
        market_cap=1.0e10,
        trailing_eps=8.0,
        book_value_per_share=60.0,
        free_cash_flow=1.0e9,
        shares_outstanding=1.0e8,
        next_earnings_date=next_earnings,
    )


def test_cheap_quiet_name_yields_value_thesis():
    closes = [100.0] * 100  # flat: no momentum, not oversold
    drafts = _gen("AAA", closes, fundamentals={"AAA": _cheap("AAA")}).generate("AAA", "")
    assert len(drafts) == 1
    d = drafts[0]
    assert d.thesis_type == "value"
    assert d.direction == "long"
    assert d.conviction == 4  # 3/3 cheap flags
    assert "value flags" in d.rationale


def test_value_requires_fundamentals_supplied():
    # The same flat name with no fundamentals stays a no-thesis (slice-20 behaviour).
    assert _gen("AAA", [100.0] * 100).generate("AAA", "") == []


def test_expensive_flat_name_yields_no_thesis():
    closes = [100.0] * 100
    pricey = Fundamentals("AAA", "2025-01-01", "USD", 1.0e10, 1.0, 10.0, 1.0e8, 1.0e8, None)
    # vs $100: E/P 1%, B/P 0.1, FCF yield 1% -> zero cheap flags.
    assert _gen("AAA", closes, fundamentals={"AAA": pricey}).generate("AAA", "") == []


def test_momentum_takes_precedence_over_value():
    closes = [100.0 * (1.01**i) for i in range(260)]  # strong uptrend
    d = _gen("AAA", closes, fundamentals={"AAA": _cheap("AAA")}).generate("AAA", "")[0]
    assert d.thesis_type == "momentum"  # price signal evaluated before value


def test_earnings_proximity_annotates_thesis():
    closes = [100.0 * (1.01**i) for i in range(260)]
    as_of = START + timedelta(days=len(closes))
    soon = (as_of + timedelta(days=3)).isoformat()
    gen = SignalThesisGenerator(
        history=PriceHistory(series={"AAA": _series(closes)}),
        as_of=as_of,
        fundamentals={"AAA": _cheap("AAA", next_earnings=soon)},
    )
    d = gen.generate("AAA", "")[0]
    assert "Earnings in 3d" in d.rationale


def test_distant_earnings_adds_no_note():
    closes = [100.0 * (1.01**i) for i in range(260)]
    as_of = START + timedelta(days=len(closes))
    far = (as_of + timedelta(days=90)).isoformat()
    gen = SignalThesisGenerator(
        history=PriceHistory(series={"AAA": _series(closes)}),
        as_of=as_of,
        fundamentals={"AAA": _cheap("AAA", next_earnings=far)},
    )
    assert "Earnings in" not in gen.generate("AAA", "")[0].rationale


def test_build_signal_generator_uses_ingested_fundamentals():
    conn = _conn()
    closes = [100.0] * 100
    save_prices(conn, "AAA", list(_series(closes)))
    save_fundamentals(conn, [_cheap("AAA")])
    as_of = START + timedelta(days=len(closes))
    drafts = build_signal_generator(conn, as_of=as_of).generate("AAA", "")
    assert len(drafts) == 1
    assert drafts[0].thesis_type == "value"
