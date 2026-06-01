"""Tests for the backtest harness."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from traders.backtest import (
    BacktestError,
    backtest_experiment,
    compare_params,
    run_backtest,
)
from traders.db import apply_migrations
from traders.optimizer import propose_experiment
from traders.parameters import LearnedParameters
from traders.prices import PriceHistory, synthetic_history
from traders.reports import render_backtest, render_backtest_comparison
from traders.strategy import StrategyGoal

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

# A goal lenient enough to render a verdict on a handful of trades.
GOAL = StrategyGoal("g", "", 5.0, 20.0, 0.5, 0.1, 4)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


# A hand-built price series so trades and PnL are exactly predictable.
# Rebalances (start Jan1, step 7): Jan1, 8, 15, 22, 29. Holding 7d.
CONTROLLED = PriceHistory(
    series={
        "AAA": (
            ("2026-01-01", 100.0),
            ("2026-01-08", 110.0),  # T1: 100 -> 110  (+10%)
            ("2026-01-15", 121.0),  # T2: 110 -> 121  (+10%)
            ("2026-01-22", 121.0),  # T3: 121 -> 121  ( 0%)
            ("2026-01-29", 110.0),  # T4: 121 -> 110  (-9.09%)
            ("2026-02-05", 121.0),  # T5: 110 -> 121  (+10%)
        )
    }
)


def _controlled_result():
    return run_backtest(
        CONTROLLED,
        params=LearnedParameters(batch_size=1, max_total_size_pct=20.0),
        goal=GOAL,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        holding_days=7,
        rebalance_every_days=7,
        watchlist=["AAA"],
    )


def test_controlled_replay_produces_expected_trades():
    result = _controlled_result()
    assert result.num_trades == 5
    assert result.skipped_no_price == 0
    assert result.metrics.num_closed == 5
    assert result.metrics.hit_rate == 0.75  # 3 wins, 1 loss, 1 flat
    first = result.trades[0]
    assert first.entry_price == 100.0
    assert first.exit_price == 110.0
    assert abs(first.pnl_pct - 10.0) < 1e-9


def test_controlled_replay_scores_on_track():
    assert _controlled_result().scorecard.verdict == "on_track"


def test_backtest_is_deterministic():
    assert _controlled_result() == _controlled_result()


def test_holding_period_sets_exit_day():
    trade = _controlled_result().trades[0]
    assert trade.entry_day == "2026-01-01"
    assert trade.exit_day == "2026-01-08"  # entry + 7 days


def test_missing_price_is_skipped_not_fatal():
    # Watchlist has a ticker with no price history at all.
    result = run_backtest(
        CONTROLLED,
        params=LearnedParameters(batch_size=2, max_total_size_pct=20.0),
        goal=GOAL,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        holding_days=7,
        rebalance_every_days=7,
        watchlist=["AAA", "ZZZ"],
    )
    assert result.skipped_no_price > 0
    assert all(t.ticker == "AAA" for t in result.trades)


def test_higher_exposure_cap_allows_more_trades():
    hist = synthetic_history(
        ["AAA", "BBB", "CCC"], date(2026, 1, 1), date(2026, 3, 31)
    )
    tight = run_backtest(
        hist,
        params=LearnedParameters(batch_size=3, max_total_size_pct=2.0),
        goal=GOAL,
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
        watchlist=["AAA", "BBB", "CCC"],
    )
    loose = run_backtest(
        hist,
        params=LearnedParameters(batch_size=3, max_total_size_pct=20.0),
        goal=GOAL,
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
        watchlist=["AAA", "BBB", "CCC"],
    )
    assert loose.num_trades >= tight.num_trades
    assert loose.num_trades > 0


def test_rejects_end_before_start():
    with pytest.raises(BacktestError):
        run_backtest(
            CONTROLLED,
            params=LearnedParameters(),
            goal=GOAL,
            start=date(2026, 2, 1),
            end=date(2026, 1, 1),
            watchlist=["AAA"],
        )


def test_compare_params_runs_both():
    hist = synthetic_history(["AAA", "BBB"], date(2026, 1, 1), date(2026, 3, 31))
    base, cand = compare_params(
        hist,
        baseline=LearnedParameters(batch_size=2, max_total_size_pct=4.0),
        candidate=LearnedParameters(batch_size=2, max_total_size_pct=20.0),
        goal=GOAL,
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
        watchlist=["AAA", "BBB"],
    )
    assert base.max_total_size_pct == 4.0
    assert cand.max_total_size_pct == 20.0


def _seed_failing_data(conn: sqlite3.Connection) -> None:
    rows = [
        ("AAA", 100, 110, "2025-01-10T00:00:00+00:00"),
        ("BBB", 100, 90, "2025-01-12T00:00:00+00:00"),
        ("CCC", 100, 120, "2025-01-14T00:00:00+00:00"),
        ("DDD", 100, 95, "2025-01-16T00:00:00+00:00"),
    ]
    for ticker, entry, exit_, closed_at in rows:
        cur = conn.execute(
            "INSERT INTO theses (ticker, thesis_type, direction, conviction,"
            " suggested_size_pct, created_at, status)"
            " VALUES (?, 'momentum', 'long', 3, 5.0,"
            " '2025-01-01T00:00:00+00:00', 'open')",
            (ticker,),
        )
        conn.execute(
            "INSERT INTO positions (thesis_id, ticker, status, size_pct,"
            " entry_price, exit_price, opened_at, closed_at)"
            " VALUES (?, ?, 'closed', 5.0, ?, ?, '2025-01-01T00:00:00+00:00', ?)",
            (cur.lastrowid, ticker, entry, exit_, closed_at),
        )
    conn.commit()


def test_backtest_experiment_compares_proposed_change():
    conn = _conn()
    _seed_failing_data(conn)
    drawdown_goal = StrategyGoal("g", "", 5.0, 5.0, 0.5, 0.1, 4)
    exp = propose_experiment(conn, goal=drawdown_goal, params=LearnedParameters())
    assert exp is not None  # drawdown fails -> lower the exposure cap (20 -> 16)

    hist = synthetic_history(["AAA", "BBB"], date(2026, 1, 1), date(2026, 3, 31))
    base, cand = backtest_experiment(
        conn,
        exp.id,
        hist,
        params=LearnedParameters(),
        goal=GOAL,
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
        watchlist=["AAA", "BBB"],
    )
    assert base.max_total_size_pct == 20.0
    assert cand.max_total_size_pct == 16.0


def test_backtest_experiment_unknown_id_raises():
    conn = _conn()
    hist = synthetic_history(["AAA"], date(2026, 1, 1), date(2026, 1, 31))
    with pytest.raises(BacktestError):
        backtest_experiment(
            conn,
            999,
            hist,
            goal=GOAL,
            start=date(2026, 1, 1),
            end=date(2026, 1, 31),
            watchlist=["AAA"],
        )


def test_backtest_never_writes_live_tables(tmp_path):
    """Core promise: the harness simulates in memory, never mutating live tables."""
    from traders.db import connect

    db = tmp_path / "t.db"
    conn = connect(db)
    apply_migrations(conn)
    tables = ("candidates", "research_notes", "theses", "positions", "pm_decisions")
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    run_backtest(
        synthetic_history(["AAA", "BBB"], date(2026, 1, 1), date(2026, 3, 31)),
        params=LearnedParameters(),
        goal=GOAL,
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
        watchlist=["AAA", "BBB"],
    )

    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    conn.close()
    assert before == after == {t: 0 for t in tables}


def test_unpriced_trades_counted_and_excluded_from_metrics():
    """A zero/garbage close opens a trade but can't be scored — counted, not hidden."""
    zero_hist = PriceHistory(
        series={
            "AAA": (
                ("2026-01-01", 0.0),
                ("2026-01-08", 0.0),
                ("2026-01-15", 0.0),
                ("2026-01-22", 0.0),
                ("2026-01-29", 0.0),
                ("2026-02-05", 0.0),
            )
        }
    )
    result = run_backtest(
        zero_hist,
        params=LearnedParameters(batch_size=1, max_total_size_pct=20.0),
        goal=GOAL,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        holding_days=7,
        rebalance_every_days=7,
        watchlist=["AAA"],
    )
    assert result.num_trades == 5
    assert result.unpriced_trades == 5  # entry price 0.0 -> pnl None
    assert result.metrics.num_closed == 0  # none scored


def test_render_backtest_text_and_markdown():
    result = _controlled_result()
    text = render_backtest(result, fmt="text")
    assert "Backtest — verdict:" in text
    assert "trades: 5" in text
    md = render_backtest(result, fmt="markdown")
    assert md.startswith("# Backtest")
    assert "| Criterion |" in md


def test_render_backtest_comparison():
    hist = synthetic_history(["AAA", "BBB"], date(2026, 1, 1), date(2026, 3, 31))
    base, cand = compare_params(
        hist,
        baseline=LearnedParameters(batch_size=2, max_total_size_pct=4.0),
        candidate=LearnedParameters(batch_size=2, max_total_size_pct=20.0),
        goal=GOAL,
        start=date(2026, 1, 1),
        end=date(2026, 3, 31),
        watchlist=["AAA", "BBB"],
    )
    text = render_backtest_comparison(base, cand, fmt="text")
    assert "Backtest comparison" in text
    md = render_backtest_comparison(base, cand, fmt="markdown")
    assert md.startswith("# Backtest Comparison")
    assert "Baseline" in md and "Candidate" in md
