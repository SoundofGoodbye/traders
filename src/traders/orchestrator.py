"""Daily and weekly orchestrators — sequence the agents end-to-end.

Wraps the four daily agents (Scout → Researcher → Analyst → Portfolio
Manager) and the weekly Reviewer into single callable entry points so a
cron job (or the user) can run one command instead of four. Each
orchestrator returns the run IDs from every step so callers can print
progress, render reports, or audit the chain.

Fail-fast: any underlying agent error propagates. No retries, no
partial-success swallowing — that is the operator's signal that
something needs attention before the next daily cycle.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from traders.analyst import run as analyst_run
from traders.portfolio import DEFAULT_MAX_TOTAL_SIZE_PCT, DailyReport
from traders.portfolio import run as pm_run
from traders.research import run as research_run
from traders.reviewer import run as reviewer_run
from traders.scout import DEFAULT_BATCH_SIZE
from traders.scout import run as scout_run


@dataclass(frozen=True)
class DailyRunResult:
    """Run IDs from every daily step plus the final PM report."""

    scout_run_id: int
    scout_picks: list[str]
    research_run_id: int
    research_tickers: list[str]
    analyst_run_id: int
    analyst_thesis_count: int
    report: DailyReport


@dataclass(frozen=True)
class WeeklyRunResult:
    """Reviewer run ID and post-mortem count from one weekly orchestration."""

    reviewer_run_id: int
    post_mortems_written: int


def run_daily(
    conn: sqlite3.Connection,
    watchlist_path: Path | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_total_size_pct: float = DEFAULT_MAX_TOTAL_SIZE_PCT,
) -> DailyRunResult:
    """Run Scout → Researcher → Analyst → Portfolio Manager against `conn`."""
    scout_id, picks = scout_run(
        conn, watchlist_path=watchlist_path, batch_size=batch_size
    )
    research_id, tickers = research_run(conn, scout_run_id=scout_id)
    analyst_id, n_theses = analyst_run(conn, research_run_id=research_id)
    report = pm_run(
        conn,
        analyst_run_id=analyst_id,
        max_total_size_pct=max_total_size_pct,
    )
    return DailyRunResult(
        scout_run_id=scout_id,
        scout_picks=picks,
        research_run_id=research_id,
        research_tickers=tickers,
        analyst_run_id=analyst_id,
        analyst_thesis_count=n_theses,
        report=report,
    )


def run_weekly(conn: sqlite3.Connection) -> WeeklyRunResult:
    """Run the weekly Reviewer against `conn`."""
    run_id, n = reviewer_run(conn)
    return WeeklyRunResult(reviewer_run_id=run_id, post_mortems_written=n)
