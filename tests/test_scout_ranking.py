"""Tests for the signal-ranked Scout (slice 21)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from traders import scout
from traders.db import apply_migrations
from traders.prices import PriceHistory

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
START = date(2025, 1, 1)


def _series(closes: list[float]) -> tuple[tuple[str, float], ...]:
    out: list[tuple[str, float]] = []
    day = START
    for c in closes:
        out.append((day.isoformat(), float(c)))
        day += timedelta(days=1)
    return tuple(out)


def _ramp(start_price: float, pct: float, n: int) -> list[float]:
    out: list[float] = []
    p = start_price
    for _ in range(n):
        p *= 1 + pct
        out.append(p)
    return out


def _universe() -> PriceHistory:
    return PriceHistory(
        series={
            "AAA": _series(_ramp(100.0, 0.01, 260)),  # strong momentum
            "BBB": _series(_ramp(100.0, 0.001, 260)),  # mild momentum
            "CCC": _series([100.0] * 240 + [100.0 - 1.5 * i for i in range(1, 21)]),  # oversold
            "DDD": _series([100.0] * 30),  # too little history
        }
    )


WATCHLIST = ["AAA", "BBB", "CCC", "DDD"]
AS_OF = START + timedelta(days=260)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


# ---- ranking --------------------------------------------------------------


def test_rank_surfaces_momentum_and_oversold_drops_no_history():
    ranked = scout.rank_candidates(WATCHLIST, _universe(), AS_OF, batch_size=2)
    assert set(ranked) == {"AAA", "CCC"}  # trend leader + washed-out name
    assert "DDD" not in ranked  # insufficient history


def test_rank_returns_empty_when_no_signals():
    empty = PriceHistory(series={})
    assert scout.rank_candidates(WATCHLIST, empty, AS_OF, batch_size=2) == []


# ---- scout.run integration ------------------------------------------------


def test_run_with_history_writes_signal_ranked(tmp_path):
    conn = _conn()
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": WATCHLIST, "eurostoxx50": []}))
    run_id, picks = scout.run(
        conn, watchlist_path=wl, run_date=AS_OF, batch_size=2, history=_universe()
    )
    assert set(picks) == {"AAA", "CCC"}
    reason = conn.execute(
        "SELECT reason FROM candidates WHERE scout_run_id = ? LIMIT 1", (run_id,)
    ).fetchone()[0]
    assert "signal-ranked" in reason


def test_run_falls_back_to_rotation_without_price_signals(tmp_path):
    conn = _conn()
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": WATCHLIST, "eurostoxx50": []}))
    run_id, picks = scout.run(
        conn,
        watchlist_path=wl,
        run_date=AS_OF,
        batch_size=2,
        history=PriceHistory(series={}),
    )
    assert len(picks) == 2  # rotation still produced candidates
    reason = conn.execute(
        "SELECT reason FROM candidates WHERE scout_run_id = ? LIMIT 1", (run_id,)
    ).fetchone()[0]
    assert "rotation fallback" in reason


def test_run_without_history_uses_rotation(tmp_path):
    conn = _conn()
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": WATCHLIST, "eurostoxx50": []}))
    run_id, picks = scout.run(conn, watchlist_path=wl, run_date=AS_OF, batch_size=2)
    assert len(picks) == 2
    reason = conn.execute(
        "SELECT reason FROM candidates WHERE scout_run_id = ? LIMIT 1", (run_id,)
    ).fetchone()[0]
    assert reason.startswith("rotation window")
