"""Tests for portfolio exposure — concentration + correlation (slice 46 / B14)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from traders.db import apply_migrations
from traders.exposure import (
    concentration,
    correlations,
    exposure_report,
    open_position_weights,
)
from traders.prices import PriceHistory

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def _open_position(conn: sqlite3.Connection, ticker: str, size_pct: float) -> None:
    cur = conn.execute(
        "INSERT INTO theses (ticker, thesis_type, direction, conviction,"
        " suggested_size_pct, created_at, status)"
        " VALUES (?, 'momentum', 'long', 3, ?, '2026-01-01T00:00:00+00:00', 'open')",
        (ticker, size_pct),
    )
    conn.execute(
        "INSERT INTO positions (thesis_id, ticker, status, size_pct, entry_price,"
        " opened_at) VALUES (?, ?, 'open', ?, 100.0, '2026-01-01T00:00:00+00:00')",
        (cur.lastrowid, ticker, size_pct),
    )
    conn.commit()


def _days(n: int) -> list[str]:
    return [date(2026, 1, 1).replace(day=i + 1).isoformat() for i in range(n)]


# ---- concentration ---------------------------------------------------------


def test_concentration_summarizes_weights():
    c = concentration([("AAA", 5.0), ("BBB", 3.0), ("CCC", 2.0)])
    assert c.num_positions == 3
    assert c.total_size_pct == 10.0
    assert c.largest_ticker == "AAA" and c.largest_pct == 5.0
    assert c.top3_pct == 10.0
    # HHI: (.5^2 + .3^2 + .2^2) = .25 + .09 + .04 = .38
    assert abs(c.herfindahl - 0.38) < 1e-9


def test_concentration_empty():
    c = concentration([])
    assert c.num_positions == 0 and c.largest_ticker is None and c.herfindahl == 0.0


# ---- correlation -----------------------------------------------------------


def test_correlations_flag_lockstep_pair():
    days = _days(30)
    a = [100.0 * (1.01**i) for i in range(30)]
    b = [50.0 * (1.01**i) for i in range(30)]  # identical returns -> corr 1
    flat = [100.0] * 30  # no variance -> skipped, never flagged
    history = PriceHistory(
        series={
            "AAA": tuple(zip(days, a)),
            "BBB": tuple(zip(days, b)),
            "CCC": tuple(zip(days, flat)),
        }
    )
    pairs = correlations(history, ["AAA", "BBB", "CCC"], as_of=date(2026, 2, 1))
    assert len(pairs) == 1
    assert {pairs[0].a, pairs[0].b} == {"AAA", "BBB"}
    assert pairs[0].correlation > 0.99


def test_correlations_need_enough_overlap():
    days = _days(10)  # below the default min_overlap of 20
    series = {t: tuple(zip(days, [100.0 * (1.01**i) for i in range(10)])) for t in ("AAA", "BBB")}
    assert correlations(PriceHistory(series=series), ["AAA", "BBB"], as_of=date(2026, 2, 1)) == []


# ---- report over the db ----------------------------------------------------


def test_exposure_report_over_open_positions():
    conn = _conn()
    _open_position(conn, "AAA", 5.0)
    _open_position(conn, "BBB", 5.0)
    assert {t for t, _ in open_position_weights(conn)} == {"AAA", "BBB"}
    days = _days(30)
    history = PriceHistory(
        series={
            "AAA": tuple(zip(days, [100.0 * (1.01**i) for i in range(30)])),
            "BBB": tuple(zip(days, [50.0 * (1.01**i) for i in range(30)])),
        }
    )
    report = exposure_report(conn, history, as_of=date(2026, 2, 1))
    assert report.concentration.num_positions == 2
    assert len(report.correlated_pairs) == 1  # AAA/BBB move together
