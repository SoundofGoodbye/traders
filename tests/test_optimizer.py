"""Tests for the self-improvement loop (optimizer + experiments ledger)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from traders.db import apply_migrations
from traders.metrics import CriterionScore, Metrics, ScoreCard
from traders.optimizer import (
    OptimizerError,
    StubOptimizer,
    apply_experiment,
    get_experiment,
    list_experiments,
    propose_experiment,
    reject_experiment,
)
from traders.parameters import LearnedParameters, load_parameters
from traders.strategy import StrategyGoal

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
CRITERIA = ("return_pct_30d", "max_drawdown_pct", "hit_rate", "sharpe_per_trade")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def _metrics() -> Metrics:
    return Metrics(
        num_closed=10,
        num_decided=10,
        num_wins=5,
        hit_rate=0.5,
        avg_pnl_pct=0.0,
        total_pnl_pct=0.0,
        return_pct_30d=0.0,
        max_drawdown_pct=0.0,
        sharpe_per_trade=0.0,
    )


def _card(verdict: str, failing: set[str]) -> ScoreCard:
    criteria = [
        CriterionScore(name=n, value=0.0, threshold=0.0, passed=n not in failing) for n in CRITERIA
    ]
    return ScoreCard(verdict=verdict, criteria=criteria, metrics=_metrics())


def _seed_failing_data(conn: sqlite3.Connection) -> None:
    # PnL by close order: +10, -10, +20, -5 -> drawdown 10, return 15.
    rows = [
        ("AAPL", 100, 110, "2025-01-10T00:00:00+00:00"),
        ("MSFT", 100, 90, "2025-01-12T00:00:00+00:00"),
        ("GOOG", 100, 120, "2025-01-14T00:00:00+00:00"),
        ("AMZN", 100, 95, "2025-01-16T00:00:00+00:00"),
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
            " VALUES (?, ?, 'closed', 5.0, ?, ?,"
            " '2025-01-01T00:00:00+00:00', ?)",
            (cur.lastrowid, ticker, entry, exit_, closed_at),
        )
    conn.commit()


# A goal where only drawdown fails on the seeded data (dd 10 > 5).
DRAWDOWN_GOAL = StrategyGoal("g", "", 5.0, 5.0, 0.5, 0.1, 4)


# ---- StubOptimizer rule ---------------------------------------------------


def test_no_proposal_when_on_track():
    assert StubOptimizer().propose(_card("on_track", set()), LearnedParameters()) is None


def test_no_proposal_when_insufficient_data():
    card = _card("insufficient_data", {"max_drawdown_pct"})
    assert StubOptimizer().propose(card, LearnedParameters()) is None


def test_drawdown_failure_lowers_exposure():
    p = StubOptimizer().propose(_card("failing", {"max_drawdown_pct"}), LearnedParameters())
    assert p is not None
    assert p.param == "max_total_size_pct"
    assert p.new_value == 16.0  # 20.0 * 0.8


def test_low_return_raises_exposure():
    p = StubOptimizer().propose(_card("failing", {"return_pct_30d"}), LearnedParameters())
    assert p is not None
    assert p.param == "max_total_size_pct"
    assert p.new_value == 24.0  # 20.0 * 1.2


def test_weak_hit_rate_widens_batch():
    p = StubOptimizer().propose(_card("failing", {"hit_rate"}), LearnedParameters())
    assert p is not None
    assert p.param == "batch_size"
    assert p.new_value == 12  # 10 + 2


def test_only_one_variable_changes():
    p = StubOptimizer().propose(
        _card("failing", {"max_drawdown_pct", "return_pct_30d"}), LearnedParameters()
    )
    # drawdown has priority; exposure is the single knob touched.
    assert p is not None
    assert p.param == "max_total_size_pct"


# ---- experiments ledger ---------------------------------------------------


def test_propose_logs_experiment_without_changing_params(tmp_path: Path):
    conn = _conn()
    _seed_failing_data(conn)
    lp = tmp_path / "learned_parameters.json"

    exp = propose_experiment(conn, goal=DRAWDOWN_GOAL, params=LearnedParameters())

    assert exp is not None
    assert exp.status == "proposed"
    assert exp.param == "max_total_size_pct"
    assert exp.old_value == "20.0"
    assert exp.new_value == "16.0"
    assert not lp.exists()  # proposing is review-only


def test_propose_returns_existing_pending(tmp_path: Path):
    conn = _conn()
    _seed_failing_data(conn)
    first = propose_experiment(conn, goal=DRAWDOWN_GOAL, params=LearnedParameters())
    second = propose_experiment(conn, goal=DRAWDOWN_GOAL, params=LearnedParameters())
    assert first is not None and second is not None
    assert first.id == second.id
    assert len(list_experiments(conn)) == 1


def test_no_proposal_returns_none_on_healthy_strategy():
    conn = _conn()
    _seed_failing_data(conn)
    healthy = StrategyGoal("g", "", 5.0, 50.0, 0.5, 0.1, 4)  # nothing fails
    assert propose_experiment(conn, goal=healthy, params=LearnedParameters()) is None


def test_apply_writes_the_one_change_and_marks_applied(tmp_path: Path):
    conn = _conn()
    _seed_failing_data(conn)
    exp = propose_experiment(conn, goal=DRAWDOWN_GOAL, params=LearnedParameters())
    assert exp is not None
    lp = tmp_path / "learned_parameters.json"

    applied = apply_experiment(conn, exp.id, params_path=lp)

    assert applied.status == "applied"
    assert applied.decided_at is not None
    saved = load_parameters(lp)
    assert saved.max_total_size_pct == 16.0
    assert saved.batch_size == 10  # untouched


def test_reject_marks_rejected_and_leaves_params(tmp_path: Path):
    conn = _conn()
    _seed_failing_data(conn)
    exp = propose_experiment(conn, goal=DRAWDOWN_GOAL, params=LearnedParameters())
    assert exp is not None
    lp = tmp_path / "learned_parameters.json"

    rejected = reject_experiment(conn, exp.id)

    assert rejected.status == "rejected"
    assert not lp.exists()


def test_apply_non_proposed_raises(tmp_path: Path):
    conn = _conn()
    _seed_failing_data(conn)
    exp = propose_experiment(conn, goal=DRAWDOWN_GOAL, params=LearnedParameters())
    assert exp is not None
    apply_experiment(conn, exp.id, params_path=tmp_path / "lp.json")

    with pytest.raises(OptimizerError):
        apply_experiment(conn, exp.id, params_path=tmp_path / "lp.json")


def test_apply_unknown_id_raises():
    conn = _conn()
    with pytest.raises(OptimizerError):
        apply_experiment(conn, 999)


def test_get_experiment_returns_none_for_missing():
    conn = _conn()
    assert get_experiment(conn, 1) is None
