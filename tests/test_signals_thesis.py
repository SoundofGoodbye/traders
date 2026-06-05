"""Tests for the signal-driven thesis generator (slice 20)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from traders import analyst, research, scout
from traders.db import apply_migrations
from traders.fundamental_periods import periods_from_statements, save_periods
from traders.fundamentals import Fundamentals, save_fundamentals
from traders.prices import PriceHistory, save_prices
from traders.quality import PiotroskiScore
from traders.signals_thesis import SignalThesisGenerator, build_signal_generator
from traders.valuation import IntrinsicValue

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


# ---- value quality gate (slice 35 / B4) ----------------------------------


def _annual(period_end: str, available_at: str, **figures) -> dict:
    return {
        "period_end": period_end,
        "period_type": "annual",
        "available_at": available_at,
        **figures,
    }


# Two annual periods whose year-over-year improves on every Piotroski test -> 9/9.
_HI_QUALITY = [
    _annual(
        "2024-12-31",
        "2025-02-15",
        revenue=1200.0,
        gross_profit=420.0,
        net_income=120.0,
        operating_cash_flow=150.0,
        total_assets=2100.0,
        current_assets=800.0,
        current_liabilities=400.0,
        long_term_debt=300.0,
        shares_outstanding=1000.0,
    ),
    _annual(
        "2023-12-31",
        "2024-02-15",
        revenue=1000.0,
        gross_profit=300.0,
        net_income=50.0,
        operating_cash_flow=60.0,
        total_assets=2000.0,
        current_assets=600.0,
        current_liabilities=400.0,
        long_term_debt=500.0,
        shares_outstanding=1000.0,
    ),
]
# Two annual periods that deteriorate on every test -> 0/9.
_LO_QUALITY = [
    _annual(
        "2024-12-31",
        "2025-02-15",
        revenue=900.0,
        gross_profit=270.0,
        net_income=-10.0,
        operating_cash_flow=-20.0,
        total_assets=2200.0,
        current_assets=440.0,
        current_liabilities=400.0,
        long_term_debt=600.0,
        shares_outstanding=1200.0,
    ),
    _annual(
        "2023-12-31",
        "2024-02-15",
        revenue=1000.0,
        gross_profit=400.0,
        net_income=100.0,
        operating_cash_flow=120.0,
        total_assets=2000.0,
        current_assets=800.0,
        current_liabilities=400.0,
        long_term_debt=200.0,
        shares_outstanding=1000.0,
    ),
]


def test_cheap_low_quality_name_is_vetoed():
    # Cheap on all three flags, but a confirmed weak F-score -> no thesis (trap).
    gen = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        quality={"AAA": PiotroskiScore(score=2, computable=9, components={})},
    )
    assert gen.generate("AAA", "") == []


def test_cheap_strong_quality_boosts_conviction_and_notes_score():
    gen = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        quality={"AAA": PiotroskiScore(score=8, computable=9, components={})},
    )
    d = gen.generate("AAA", "")[0]
    assert d.thesis_type == "value"
    assert d.conviction == 5  # 3/3 flags (base 4) + strong quality -> +1
    assert "Piotroski 8/9" in d.rationale


def test_cheap_passing_quality_keeps_conviction():
    gen = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        quality={"AAA": PiotroskiScore(score=5, computable=9, components={})},
    )
    d = gen.generate("AAA", "")[0]
    assert d.conviction == 4  # passes the gate but not strong -> unchanged
    assert "Piotroski 5/9" in d.rationale


def test_cheap_unknown_quality_falls_back_to_cheap_only():
    # The quality map exists but has no entry for this name -> no veto, no note.
    gen = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        quality={"BBB": PiotroskiScore(score=1, computable=9, components={})},
    )
    d = gen.generate("AAA", "")[0]
    assert d.thesis_type == "value"
    assert d.conviction == 4
    assert "Piotroski" not in d.rationale


def test_cheap_sparse_quality_falls_back_not_vetoed():
    # A low score with too few computable tests to trust -> fall back, don't veto.
    gen = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        quality={"AAA": PiotroskiScore(score=1, computable=3, components={})},
    )
    d = gen.generate("AAA", "")[0]
    assert d.thesis_type == "value"
    assert "Piotroski" not in d.rationale


def test_build_signal_generator_vetoes_low_quality_value():
    conn = _conn()
    closes = [100.0] * 100
    save_prices(conn, "AAA", list(_series(closes)))
    save_fundamentals(conn, [_cheap("AAA")])
    save_periods(conn, periods_from_statements("AAA", _LO_QUALITY))
    as_of = START + timedelta(days=len(closes))
    assert build_signal_generator(conn, as_of=as_of).generate("AAA", "") == []


def test_build_signal_generator_passes_strong_quality_value():
    conn = _conn()
    closes = [100.0] * 100
    save_prices(conn, "AAA", list(_series(closes)))
    save_fundamentals(conn, [_cheap("AAA")])
    save_periods(conn, periods_from_statements("AAA", _HI_QUALITY))
    as_of = START + timedelta(days=len(closes))
    d = build_signal_generator(conn, as_of=as_of).generate("AAA", "")[0]
    assert d.thesis_type == "value"
    assert d.conviction == 5
    assert "Piotroski 9/9" in d.rationale


# ---- value margin-of-safety gate (slice 36 / B3) -------------------------


def _iv(margin_of_safety: float, *, iv_ps: float = 140.0, years: int = 5) -> IntrinsicValue:
    return IntrinsicValue(
        owner_earnings=11000.0,
        years=years,
        shares=1000.0,
        price=100.0,
        iv_total=iv_ps * 1000.0,
        iv_per_share=iv_ps,
        margin_of_safety=margin_of_safety,
        buy_below_price=iv_ps * 0.8,
        implied_growth=0.01,
        discount_rate=0.10,
        terminal_growth=0.02,
    )


def _improving(cfo_cur: float, cfo_prev: float) -> list[dict]:
    """An improving annual pair (Piotroski 9/9) with tunable cash flow for MoS."""
    return [
        _annual(
            "2024-12-31",
            "2025-02-15",
            revenue=12000.0,
            gross_profit=4200.0,
            net_income=6000.0,
            operating_cash_flow=cfo_cur,
            capital_expenditure=-300.0,
            total_assets=21000.0,
            current_assets=8000.0,
            current_liabilities=4000.0,
            long_term_debt=3000.0,
            shares_outstanding=1000.0,
        ),
        _annual(
            "2023-12-31",
            "2024-02-15",
            revenue=10000.0,
            gross_profit=3000.0,
            net_income=5000.0,
            operating_cash_flow=cfo_prev,
            capital_expenditure=-300.0,
            total_assets=20000.0,
            current_assets=6000.0,
            current_liabilities=4000.0,
            long_term_debt=5000.0,
            shares_outstanding=1000.0,
        ),
    ]


def test_cheap_without_margin_of_safety_is_vetoed():
    # Cheap on flags, but priced only 5% below intrinsic value -> no margin -> skip.
    gen = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        valuation={"AAA": _iv(0.05)},
    )
    assert gen.generate("AAA", "") == []


def test_margin_of_safety_drives_conviction():
    d = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        valuation={"AAA": _iv(0.55)},  # deep discount -> conviction 5
    ).generate("AAA", "")[0]
    assert d.conviction == 5
    assert "margin of safety 55%" in d.rationale


def test_modest_margin_of_safety_lowers_conviction_below_flag_base():
    # 25% MoS -> band 3, overriding the flag-based 4 (MoS drives conviction).
    d = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        valuation={"AAA": _iv(0.25)},
    ).generate("AAA", "")[0]
    assert d.conviction == 3


def test_margin_of_safety_and_quality_compound():
    d = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        valuation={"AAA": _iv(0.40)},  # band 4
        quality={"AAA": PiotroskiScore(score=8, computable=9, components={})},  # +1
    ).generate("AAA", "")[0]
    assert d.conviction == 5
    assert "margin of safety 40%" in d.rationale and "Piotroski 8/9" in d.rationale


def test_unknown_valuation_falls_back_to_flag_conviction():
    d = _gen(
        "AAA",
        [100.0] * 100,
        fundamentals={"AAA": _cheap("AAA")},
        valuation={"BBB": _iv(0.05)},  # no entry for AAA
    ).generate("AAA", "")[0]
    assert d.thesis_type == "value"
    assert d.conviction == 4
    assert "margin of safety" not in d.rationale


def test_build_signal_generator_vetoes_when_no_margin_of_safety():
    conn = _conn()
    closes = [100.0] * 100
    save_prices(conn, "AAA", list(_series(closes)))
    save_fundamentals(conn, [_cheap("AAA")])
    save_periods(conn, periods_from_statements("AAA", _improving(8300.0, 8000.0)))  # IV ~= price
    as_of = START + timedelta(days=len(closes))
    assert build_signal_generator(conn, as_of=as_of).generate("AAA", "") == []


def test_build_signal_generator_value_with_margin_of_safety():
    conn = _conn()
    closes = [100.0] * 100
    save_prices(conn, "AAA", list(_series(closes)))
    save_fundamentals(conn, [_cheap("AAA")])
    save_periods(conn, periods_from_statements("AAA", _improving(11500.0, 11000.0)))  # IV ~140
    as_of = START + timedelta(days=len(closes))
    d = build_signal_generator(conn, as_of=as_of).generate("AAA", "")[0]
    assert d.thesis_type == "value"
    assert "margin of safety" in d.rationale
    assert "Piotroski 9/9" in d.rationale  # same improving periods score 9
    assert d.conviction == 4  # MoS ~28% (band 3) + strong quality (+1)
