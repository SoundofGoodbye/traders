"""Tests for realized metrics and the goal scorer."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from traders.db import apply_migrations
from traders.metrics import compute_and_score, compute_metrics, score
from traders.strategy import StrategyGoal

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
NOW = datetime(2025, 1, 31, tzinfo=timezone.utc)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def _seed(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    entry: float,
    exit_: float,
    closed_at: str,
    direction: str = "long",
) -> None:
    cur = conn.execute(
        "INSERT INTO theses (ticker, thesis_type, direction, conviction,"
        " suggested_size_pct, created_at, status)"
        " VALUES (?, 'momentum', ?, 3, 5.0, '2025-01-01T00:00:00+00:00', 'open')",
        (ticker, direction),
    )
    conn.execute(
        "INSERT INTO positions (thesis_id, ticker, status, size_pct, entry_price,"
        " exit_price, opened_at, closed_at) VALUES (?, ?, 'closed', 5.0, ?, ?,"
        " '2025-01-01T00:00:00+00:00', ?)",
        (cur.lastrowid, ticker, entry, exit_, closed_at),
    )
    conn.commit()


def _seed_four(conn: sqlite3.Connection) -> None:
    # PnL sequence (by close order): +10, -10, +20, -5
    _seed(conn, ticker="AAPL", entry=100, exit_=110, closed_at="2025-01-10T00:00:00+00:00")
    _seed(conn, ticker="MSFT", entry=100, exit_=90, closed_at="2025-01-12T00:00:00+00:00")
    _seed(conn, ticker="GOOG", entry=100, exit_=120, closed_at="2025-01-14T00:00:00+00:00")
    _seed(conn, ticker="AMZN", entry=100, exit_=95, closed_at="2025-01-16T00:00:00+00:00")


def test_empty_db_yields_zeroed_metrics_and_insufficient_verdict():
    conn = _conn()
    goal = StrategyGoal("g", "", 5.0, 20.0, 0.5, 0.1, 10)

    card = compute_and_score(conn, goal, now=NOW)

    assert card.metrics.num_closed == 0
    assert card.metrics.hit_rate is None
    assert card.metrics.total_pnl_pct == 0.0
    assert card.verdict == "insufficient_data"


def test_basic_aggregates():
    conn = _conn()
    _seed_four(conn)

    m = compute_metrics(conn, now=NOW)

    assert m.num_closed == 4
    assert m.num_wins == 2
    assert m.num_decided == 4
    assert m.hit_rate == 0.5
    assert m.total_pnl_pct == 15.0
    assert m.avg_pnl_pct == 3.75
    assert m.return_pct_30d == 15.0  # all four closed within the trailing 30d


def test_max_drawdown_tracks_peak_to_trough():
    conn = _conn()
    _seed_four(conn)

    m = compute_metrics(conn, now=NOW)

    # cumulative: 10, 0, 20, 15 -> worst peak-to-trough dip is 10 -> 0.
    assert m.max_drawdown_pct == 10.0


def test_short_direction_pnl_is_inverted():
    conn = _conn()
    _seed(
        conn,
        ticker="TSLA",
        entry=100,
        exit_=80,
        closed_at="2025-01-10T00:00:00+00:00",
        direction="short",
    )

    m = compute_metrics(conn, now=NOW)

    assert m.total_pnl_pct == 20.0  # short 100->80 is +20%


def test_return_30d_excludes_stale_trades():
    conn = _conn()
    _seed(conn, ticker="OLD", entry=100, exit_=150, closed_at="2024-06-01T00:00:00+00:00")
    _seed(conn, ticker="NEW", entry=100, exit_=110, closed_at="2025-01-20T00:00:00+00:00")

    m = compute_metrics(conn, now=NOW)

    assert m.total_pnl_pct == 60.0  # both count toward all-time
    assert m.return_pct_30d == 10.0  # only NEW is inside the window


def test_on_track_when_all_criteria_pass():
    conn = _conn()
    _seed_four(conn)
    goal = StrategyGoal("g", "", 5.0, 20.0, 0.5, 0.1, 4)

    card = compute_and_score(conn, goal, now=NOW)

    assert card.verdict == "on_track"
    assert all(c.passed for c in card.criteria)


def test_failing_when_a_criterion_misses():
    conn = _conn()
    _seed_four(conn)
    goal = StrategyGoal("g", "", 5.0, 20.0, 0.5, 1.0, 4)  # min_sharpe too high

    card = compute_and_score(conn, goal, now=NOW)

    assert card.verdict == "failing"
    sharpe = next(c for c in card.criteria if c.name == "sharpe_per_trade")
    assert sharpe.passed is False


def test_insufficient_data_overrides_pass_fail():
    conn = _conn()
    _seed_four(conn)
    goal = StrategyGoal("g", "", 5.0, 20.0, 0.5, 0.1, 10)  # needs 10 closed

    card = compute_and_score(conn, goal, now=NOW)

    assert card.verdict == "insufficient_data"


def test_score_is_pure_given_metrics():
    conn = _conn()
    _seed_four(conn)
    m = compute_metrics(conn, now=NOW)
    goal = StrategyGoal("g", "", 5.0, 20.0, 0.5, 0.1, 4)

    assert score(m, goal).verdict == score(m, goal).verdict


def test_metrics_from_trades_matches_db_path():
    """The extracted pure aggregator reproduces the DB-backed numbers."""
    from traders.metrics import ClosedTrade, metrics_from_trades

    # Same PnL sequence as _seed_four: +10, -10, +20, -5.
    trades = [
        ClosedTrade("AAPL", "long", 10.0, "2025-01-10T00:00:00+00:00"),
        ClosedTrade("MSFT", "long", -10.0, "2025-01-12T00:00:00+00:00"),
        ClosedTrade("GOOG", "long", 20.0, "2025-01-14T00:00:00+00:00"),
        ClosedTrade("AMZN", "long", -5.0, "2025-01-16T00:00:00+00:00"),
    ]
    m = metrics_from_trades(trades, now=NOW)

    assert m.num_closed == 4
    assert m.total_pnl_pct == 15.0
    assert m.max_drawdown_pct == 10.0
    assert m.hit_rate == 0.5


def test_return_30d_includes_cutoff_date_for_date_only_closed_at():
    """A date-only close (as the backtest emits) on the exact 30d boundary counts."""
    from traders.metrics import ClosedTrade, metrics_from_trades

    # NOW is 2025-01-31; 30 days earlier is 2025-01-01. The backtest stores
    # closed_at as a date-only string — it must not be excluded at the boundary.
    trades = [ClosedTrade("X", "long", 7.0, "2025-01-01")]
    m = metrics_from_trades(trades, now=NOW)

    assert m.return_pct_30d == 7.0
