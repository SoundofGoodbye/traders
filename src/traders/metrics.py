"""Realized performance metrics + scorer.

Pure functions over the closed-positions history. Computes the numbers a
``StrategyGoal`` is defined in terms of — trailing-30d return, max
drawdown, hit rate, and a per-trade Sharpe proxy — then scores them
criterion by criterion into a verdict the Reviewer, the Optimizer
(slice 16), and the web UI can read.

No mutation, no I/O beyond a single SELECT. The Sharpe figure is a
per-trade proxy (mean PnL / stdev PnL across closed trades), **not** an
annualized Sharpe ratio; it is named ``sharpe_per_trade`` so nobody
mistakes it for the real thing.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from traders.post_mortems import compute_pnl_pct
from traders.strategy import StrategyGoal

WINDOW_DAYS_30 = 30


@dataclass(frozen=True)
class ClosedTrade:
    ticker: str
    direction: str
    pnl_pct: float
    closed_at: str


@dataclass(frozen=True)
class Metrics:
    num_closed: int
    num_decided: int
    num_wins: int
    hit_rate: float | None
    avg_pnl_pct: float | None
    total_pnl_pct: float
    return_pct_30d: float
    max_drawdown_pct: float
    sharpe_per_trade: float | None


@dataclass(frozen=True)
class CriterionScore:
    name: str
    value: float | None
    threshold: float
    passed: bool


@dataclass(frozen=True)
class ScoreCard:
    verdict: str  # "on_track" | "failing" | "insufficient_data"
    criteria: list[CriterionScore]
    metrics: Metrics


def closed_trades(conn: sqlite3.Connection) -> list[ClosedTrade]:
    """Closed positions with a computable PnL, oldest close first."""
    rows = conn.execute(
        "SELECT p.ticker, t.direction, p.entry_price, p.exit_price, p.closed_at"
        " FROM positions p JOIN theses t ON t.id = p.thesis_id"
        " WHERE p.status = 'closed' AND p.closed_at IS NOT NULL"
        " ORDER BY p.closed_at, p.id"
    ).fetchall()
    trades: list[ClosedTrade] = []
    for ticker, direction, entry, exit_, closed_at in rows:
        pnl = compute_pnl_pct(direction, entry, exit_)
        if pnl is None:
            continue
        trades.append(ClosedTrade(ticker, direction, pnl, closed_at))
    return trades


def _max_drawdown_pct(trades: list[ClosedTrade]) -> float:
    """Peak-to-trough decline of the cumulative-PnL curve, as a positive %."""
    cum = peak = max_dd = 0.0
    for trade in trades:
        cum += trade.pnl_pct
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


def _return_30d(trades: list[ClosedTrade], now: datetime) -> float:
    # Compare on a date-only cutoff so the window is identical whether closed_at
    # is a full timestamp (live path) or a date-only string (backtest path). A
    # datetime cutoff would lexically exclude a date-only boundary-day close.
    cutoff = (now - timedelta(days=WINDOW_DAYS_30)).date().isoformat()
    return sum(t.pnl_pct for t in trades if t.closed_at >= cutoff)


def _now_from(trades: list[ClosedTrade], now: datetime | None) -> datetime:
    if now is not None:
        return now
    if trades:
        return datetime.fromisoformat(max(t.closed_at for t in trades))
    return datetime.now(timezone.utc)


def metrics_from_trades(
    trades: list[ClosedTrade], now: datetime | None = None
) -> Metrics:
    """Aggregate realized metrics over an explicit, ordered trade list.

    ``trades`` must be ordered oldest-close-first — ``max_drawdown_pct`` walks
    the cumulative-PnL curve in sequence. The DB path (``compute_metrics``)
    guarantees this with ``ORDER BY closed_at``; the backtest harness sorts
    before calling.
    """
    reference = _now_from(trades, now)
    pnls = [t.pnl_pct for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    decided = wins + losses
    sharpe = (
        statistics.mean(pnls) / statistics.pstdev(pnls)
        if len(pnls) >= 2 and statistics.pstdev(pnls) > 0
        else None
    )
    return Metrics(
        num_closed=len(trades),
        num_decided=decided,
        num_wins=wins,
        hit_rate=(wins / decided) if decided else None,
        avg_pnl_pct=(sum(pnls) / len(pnls)) if pnls else None,
        total_pnl_pct=sum(pnls),
        return_pct_30d=_return_30d(trades, reference),
        max_drawdown_pct=_max_drawdown_pct(trades),
        sharpe_per_trade=sharpe,
    )


def compute_metrics(conn: sqlite3.Connection, now: datetime | None = None) -> Metrics:
    """Aggregate realized metrics over all closed positions."""
    return metrics_from_trades(closed_trades(conn), now)


def score(metrics: Metrics, goal: StrategyGoal) -> ScoreCard:
    """Grade metrics against a goal, criterion by criterion."""
    criteria = [
        CriterionScore(
            "return_pct_30d",
            metrics.return_pct_30d,
            goal.target_return_pct_30d,
            metrics.return_pct_30d >= goal.target_return_pct_30d,
        ),
        CriterionScore(
            "max_drawdown_pct",
            metrics.max_drawdown_pct,
            goal.max_drawdown_pct,
            metrics.max_drawdown_pct <= goal.max_drawdown_pct,
        ),
        CriterionScore(
            "hit_rate",
            metrics.hit_rate,
            goal.min_hit_rate,
            metrics.hit_rate is not None and metrics.hit_rate >= goal.min_hit_rate,
        ),
        CriterionScore(
            "sharpe_per_trade",
            metrics.sharpe_per_trade,
            goal.min_sharpe,
            metrics.sharpe_per_trade is not None
            and metrics.sharpe_per_trade >= goal.min_sharpe,
        ),
    ]
    if metrics.num_closed < goal.min_closed_for_verdict:
        verdict = "insufficient_data"
    elif all(c.passed for c in criteria):
        verdict = "on_track"
    else:
        verdict = "failing"
    return ScoreCard(verdict=verdict, criteria=criteria, metrics=metrics)


def compute_and_score(
    conn: sqlite3.Connection, goal: StrategyGoal, now: datetime | None = None
) -> ScoreCard:
    """Convenience: compute metrics then score them."""
    return score(compute_metrics(conn, now), goal)
