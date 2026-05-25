import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from traders.analyst import run as analyst_run
from traders.db import apply_migrations
from traders.portfolio import DailyReport, ReportItem, run as pm_run
from traders.reports import (
    ReviewItem,
    latest_reviewer_run_id,
    load_review_for_run,
    render_daily_report_markdown,
    render_weekly_review_markdown,
)
from traders.research import run as research_run
from traders.reviewer import run as reviewer_run
from traders.scout import run as scout_run

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_migrations(conn, MIGRATIONS)
    yield conn
    conn.close()


def _item(thesis_id, ticker, decision, reason, **kwargs):
    return ReportItem(
        thesis_id=thesis_id,
        ticker=ticker,
        thesis_type=kwargs.get("thesis_type", "value"),
        direction=kwargs.get("direction", "long"),
        conviction=kwargs.get("conviction", 3),
        suggested_size_pct=kwargs.get("size", 2.0),
        decision=decision,
        reason=reason,
    )


def test_render_daily_report_markdown_includes_accepted_and_rejected():
    report = DailyReport(
        pm_run_id=1,
        analyst_run_id=4,
        accepted=[_item(10, "AAA", "accepted", "value thesis, conviction 3, size 2.0%")],
        rejected=[
            _item(11, "BBB", "rejected", "concentration: existing open position in BBB (3.0%)")
        ],
    )
    md = render_daily_report_markdown(report)
    assert md.startswith("# Daily Report — PM run 1")
    assert "Analyst run: 4" in md
    assert "## Accepted" in md
    assert "## Rejected" in md
    assert "**AAA**" in md
    assert "**BBB**" in md
    assert "concentration" in md
    assert "thesis 10" in md
    assert "thesis 11" in md


def test_render_daily_report_markdown_empty_sections_render_none():
    report = DailyReport(pm_run_id=2, analyst_run_id=5, accepted=[], rejected=[])
    md = render_daily_report_markdown(report)
    assert "PM run 2" in md
    assert md.count("_None._") == 2


def test_render_daily_report_markdown_zero_run_id_says_no_analyst_run():
    report = DailyReport(pm_run_id=0, analyst_run_id=0, accepted=[], rejected=[])
    md = render_daily_report_markdown(report)
    assert "No analyst run" in md


def test_render_weekly_review_markdown_renders_items():
    items = [
        ReviewItem(
            post_mortem_id=1,
            position_id=7,
            ticker="AAA",
            direction="long",
            thesis_type="value",
            conviction=3,
            size_pct=2.0,
            entry_price=100.0,
            exit_price=120.0,
            opened_at="2026-05-01",
            closed_at="2026-05-20",
            outcome="AAA long: +20.00% (win).",
            lessons="[stub] value thesis lessons.",
        )
    ]
    md = render_weekly_review_markdown(items, reviewer_run_id=2)
    assert md.startswith("# Weekly Review — reviewer run 2")
    assert "AAA" in md
    assert "position 7" in md
    assert "PnL: +20.00%" in md
    assert "**Outcome:**" in md
    assert "**Lessons:**" in md


def test_render_weekly_review_markdown_unknown_pnl_when_prices_missing():
    items = [
        ReviewItem(
            post_mortem_id=1,
            position_id=7,
            ticker="AAA",
            direction="long",
            thesis_type="value",
            conviction=3,
            size_pct=2.0,
            entry_price=None,
            exit_price=None,
            opened_at="2026-05-01",
            closed_at="2026-05-20",
            outcome="AAA long: unknown.",
            lessons="[stub]",
        )
    ]
    md = render_weekly_review_markdown(items, reviewer_run_id=1)
    assert "PnL: unknown" in md


def test_render_weekly_review_markdown_empty_items_says_none():
    md = render_weekly_review_markdown([], reviewer_run_id=3)
    assert "No post-mortems" in md
    assert "reviewer run 3" in md


def test_render_weekly_review_markdown_none_run_id_says_no_post_mortems():
    md = render_weekly_review_markdown([], reviewer_run_id=None)
    assert "No post-mortems" in md


def _seed_thesis(db, tmp_path, ticker, run_date):
    wl = tmp_path / f"wl_{run_date.isoformat()}_{ticker}.json"
    wl.write_text(json.dumps({"sp100": [ticker], "eurostoxx50": []}))
    scout_id, _ = scout_run(db, watchlist_path=wl, run_date=run_date, batch_size=1)
    research_id, _ = research_run(db, scout_run_id=scout_id)
    analyst_run(db, research_run_id=research_id)
    row = db.execute(
        "SELECT id FROM theses WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return int(row[0])


def test_load_review_for_run_joins_positions_and_theses(db, tmp_path):
    thesis_id = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    db.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, closed_at, entry_price, exit_price,"
        " size_pct, status)"
        " VALUES ('AAA', ?, '2026-05-01', '2026-05-20', 100.0, 110.0, 2.0, 'closed')",
        (thesis_id,),
    )
    db.commit()
    run_id, n = reviewer_run(db)
    assert n == 1
    items = load_review_for_run(db, run_id)
    assert len(items) == 1
    item = items[0]
    assert item.ticker == "AAA"
    assert item.entry_price == 100.0
    assert item.exit_price == 110.0
    assert item.direction in ("long", "short")
    assert item.thesis_type in ("value", "catalyst", "momentum", "mean-reversion")
    assert item.outcome
    assert item.lessons


def test_latest_reviewer_run_id_tracks_most_recent_run(db, tmp_path):
    assert latest_reviewer_run_id(db) is None
    thesis_id = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    db.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, closed_at, entry_price, exit_price,"
        " size_pct, status)"
        " VALUES ('AAA', ?, '2026-05-01', '2026-05-20', 100.0, 110.0, 2.0, 'closed')",
        (thesis_id,),
    )
    db.commit()
    reviewer_run(db)
    assert latest_reviewer_run_id(db) == 1


def test_pm_then_render_markdown_end_to_end(db, tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    scout_id, _ = scout_run(db, watchlist_path=wl, run_date=date(2026, 5, 20), batch_size=2)
    research_id, _ = research_run(db, scout_run_id=scout_id)
    analyst_id, _ = analyst_run(db, research_run_id=research_id)
    report = pm_run(db, analyst_run_id=analyst_id)
    md = render_daily_report_markdown(report)
    assert "PM run" in md
    assert "AAA" in md
    assert "BBB" in md
