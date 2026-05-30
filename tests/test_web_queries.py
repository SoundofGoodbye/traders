import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from traders.analyst import run as analyst_run
from traders.db import apply_migrations
from traders.feedback import record_fill, record_sell
from traders.portfolio import run as pm_run
from traders.research import run as research_run
from traders.scout import run as scout_run
from traders.web import queries

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_migrations(conn, MIGRATIONS)
    yield conn
    conn.close()


def _seed_daily(db, tmp_path, tickers, run_date):
    wl = tmp_path / f"wl_{run_date.isoformat()}.json"
    wl.write_text(json.dumps({"sp100": tickers, "eurostoxx50": []}))
    scout_id, _ = scout_run(db, watchlist_path=wl, run_date=run_date, batch_size=len(tickers))
    research_id, _ = research_run(db, scout_run_id=scout_id)
    analyst_id, _ = analyst_run(db, research_run_id=research_id)
    report = pm_run(db, analyst_run_id=analyst_id)
    return scout_id, analyst_id, report


def test_latest_run_ids_none_on_empty(db):
    assert queries.latest_scout_run_id(db) is None
    assert queries.latest_analyst_run_id(db) is None
    assert queries.latest_pm_run_id(db) is None


def test_candidates_for_run(db, tmp_path):
    scout_id, _, _ = _seed_daily(db, tmp_path, ["AAA", "BBB"], date(2026, 5, 20))
    assert queries.latest_scout_run_id(db) == scout_id
    cands = queries.candidates_for_run(db, scout_id)
    assert {c.ticker for c in cands} == {"AAA", "BBB"}
    assert all(c.scout_run_id == scout_id for c in cands)


def test_theses_for_run_and_by_id(db, tmp_path):
    _, analyst_id, _ = _seed_daily(db, tmp_path, ["AAA"], date(2026, 5, 20))
    theses = queries.theses_for_run(db, analyst_id)
    assert len(theses) == 1
    t = theses[0]
    assert t.ticker == "AAA"
    assert t.direction in ("long", "short")
    assert queries.thesis_by_id(db, t.id) == t


def test_thesis_by_id_missing_returns_none(db):
    assert queries.thesis_by_id(db, 999) is None


def test_picks_for_run_joins_thesis_and_orders_accepted_first(db, tmp_path):
    _, _, report = _seed_daily(db, tmp_path, ["AAA", "BBB", "CCC"], date(2026, 5, 20))
    picks = queries.picks_for_run(db, report.pm_run_id)
    assert len(picks) == len(report.accepted) + len(report.rejected)
    decisions = [p.decision for p in picks]
    # every accepted decision precedes every rejected one
    assert decisions == sorted(decisions, key=lambda d: 0 if d == "accepted" else 1)
    assert all(p.thesis.ticker for p in picks)


def test_open_positions_carry_direction(db, tmp_path):
    _, analyst_id, _ = _seed_daily(db, tmp_path, ["AAA"], date(2026, 5, 20))
    thesis_id = db.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    record_fill(db, thesis_id=thesis_id, price=100.0)
    opens = queries.open_positions(db)
    assert len(opens) == 1
    assert opens[0].ticker == "AAA"
    assert opens[0].status == "open"
    assert opens[0].direction in ("long", "short")
    assert opens[0].entry_price == 100.0


def test_open_position_for_thesis(db, tmp_path):
    _, analyst_id, _ = _seed_daily(db, tmp_path, ["AAA"], date(2026, 5, 20))
    thesis_id = db.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    # no position yet
    assert queries.open_position_for_thesis(db, thesis_id) is None
    event = record_fill(db, thesis_id=thesis_id, price=100.0)
    assert queries.open_position_for_thesis(db, thesis_id) == event.position_id
    # once sold, the thesis no longer has an *open* position
    record_sell(db, price=110.0, position_id=event.position_id)
    assert queries.open_position_for_thesis(db, thesis_id) is None


def test_recently_closed_positions(db, tmp_path):
    _, _, _ = _seed_daily(db, tmp_path, ["AAA"], date(2026, 5, 20))
    thesis_id = db.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    event = record_fill(db, thesis_id=thesis_id, price=100.0)
    record_sell(db, price=120.0, position_id=event.position_id)
    assert queries.open_positions(db) == []
    closed = queries.recently_closed_positions(db)
    assert len(closed) == 1
    assert closed[0].exit_price == 120.0
    assert closed[0].status == "closed"


def test_parse_sources():
    assert queries.parse_sources(None) == []
    assert queries.parse_sources("") == []
    assert queries.parse_sources('["a", "b"]') == ["a", "b"]
    # non-JSON falls back to a single-item list rather than raising
    assert queries.parse_sources("just a string") == ["just a string"]
