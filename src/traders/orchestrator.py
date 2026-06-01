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
from traders.data_sources import DataSource
from traders.parameters import LearnedParameters, load_parameters
from traders.portfolio import DailyReport
from traders.portfolio import run as pm_run
from traders.post_mortems import PostMortemGenerator
from traders.prices import PriceHistory
from traders.research import run as research_run
from traders.reviewer import run as reviewer_run
from traders.scout import run as scout_run
from traders.signals import ThesisGenerator


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
    batch_size: int | None = None,
    max_total_size_pct: float | None = None,
    data_source: DataSource | None = None,
    params: LearnedParameters | None = None,
    analyst_generator: ThesisGenerator | None = None,
    scout_history: PriceHistory | None = None,
) -> DailyRunResult:
    """Run Scout → Researcher → Analyst → Portfolio Manager against `conn`.

    Learned parameters are loaded once and threaded to the agents;
    explicit `batch_size` / `max_total_size_pct` still override them.
    ``analyst_generator`` lets a caller swap the stub thesis generator for the
    signal-driven one; ``None`` keeps the Analyst's default (stub).
    """
    p = params or load_parameters()
    scout_id, picks = scout_run(
        conn,
        watchlist_path=watchlist_path,
        batch_size=batch_size,
        params=p,
        history=scout_history,
    )
    research_id, tickers = research_run(conn, data_source=data_source, scout_run_id=scout_id)
    analyst_id, n_theses = analyst_run(
        conn, generator=analyst_generator, research_run_id=research_id
    )
    report = pm_run(
        conn,
        analyst_run_id=analyst_id,
        max_total_size_pct=max_total_size_pct,
        params=p,
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


def run_weekly(
    conn: sqlite3.Connection,
    reviewer_generator: PostMortemGenerator | None = None,
) -> WeeklyRunResult:
    """Run the weekly Reviewer against `conn`.

    ``reviewer_generator`` selects the post-mortem writer (default: the stub);
    pass an ``LLMPostMortemGenerator`` for Claude-written lessons.
    """
    run_id, n = reviewer_run(conn, generator=reviewer_generator)
    return WeeklyRunResult(reviewer_run_id=run_id, post_mortems_written=n)
