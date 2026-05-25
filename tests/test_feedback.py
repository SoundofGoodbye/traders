import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from traders.analyst import run as analyst_run
from traders.db import apply_migrations
from traders.feedback import (
    FeedbackError,
    record_fill,
    record_partial,
    record_sell,
    record_skip,
)
from traders.research import run as research_run
from traders.scout import run as scout_run

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_migrations(conn, MIGRATIONS)
    yield conn
    conn.close()


def _seed_thesis(db, tmp_path, ticker, run_date):
    wl = tmp_path / f"wl_{run_date.isoformat()}_{ticker}.json"
    wl.write_text(json.dumps({"sp100": [ticker], "eurostoxx50": []}))
    scout_id, _ = scout_run(db, watchlist_path=wl, run_date=run_date, batch_size=1)
    research_id, _ = research_run(db, scout_run_id=scout_id)
    analyst_run(db, research_run_id=research_id)
    row = db.execute(
        "SELECT id, suggested_size_pct FROM theses"
        " WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return int(row[0]), float(row[1])


def test_record_fill_opens_position_at_suggested_size(db, tmp_path):
    thesis_id, suggested = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    event = record_fill(db, thesis_id=thesis_id, price=101.5)
    assert event.action == "fill"
    assert event.price == 101.5
    assert event.size_pct == suggested
    assert event.position_id is not None
    pos = db.execute(
        "SELECT ticker, thesis_id, entry_price, size_pct, status, exit_price, closed_at"
        " FROM positions WHERE id = ?",
        (event.position_id,),
    ).fetchone()
    assert pos == ("AAA", thesis_id, 101.5, suggested, "open", None, None)
    fb = db.execute(
        "SELECT thesis_id, action, price, size_pct, position_id"
        " FROM feedback WHERE id = ?",
        (event.feedback_id,),
    ).fetchone()
    assert fb == (thesis_id, "fill", 101.5, suggested, event.position_id)


def test_record_fill_with_size_override(db, tmp_path):
    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    event = record_fill(db, thesis_id=thesis_id, price=100.0, size_pct=1.5)
    assert event.size_pct == 1.5
    size = db.execute(
        "SELECT size_pct FROM positions WHERE id = ?", (event.position_id,)
    ).fetchone()[0]
    assert size == 1.5


def test_record_fill_unknown_thesis_raises(db):
    with pytest.raises(FeedbackError, match="no thesis"):
        record_fill(db, thesis_id=999, price=100.0)


def test_record_fill_rejects_when_position_already_open(db, tmp_path):
    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    record_fill(db, thesis_id=thesis_id, price=100.0)
    with pytest.raises(FeedbackError, match="already has an open position"):
        record_fill(db, thesis_id=thesis_id, price=105.0)


def test_record_partial_opens_position_with_explicit_size(db, tmp_path):
    thesis_id, suggested = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    event = record_partial(db, thesis_id=thesis_id, price=99.0, size_pct=0.8)
    assert event.action == "partial"
    assert event.size_pct == 0.8
    assert event.size_pct != suggested
    pos = db.execute(
        "SELECT entry_price, size_pct, status FROM positions WHERE id = ?",
        (event.position_id,),
    ).fetchone()
    assert pos == (99.0, 0.8, "open")


def test_record_partial_rejects_when_position_already_open(db, tmp_path):
    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    record_fill(db, thesis_id=thesis_id, price=100.0)
    with pytest.raises(FeedbackError, match="already has an open position"):
        record_partial(db, thesis_id=thesis_id, price=99.0, size_pct=0.5)


def test_record_skip_logs_without_opening_position(db, tmp_path):
    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    event = record_skip(db, thesis_id=thesis_id, notes="not in budget today")
    assert event.action == "skip"
    assert event.position_id is None
    assert event.price is None
    assert event.size_pct is None
    n_positions = db.execute(
        "SELECT COUNT(*) FROM positions WHERE thesis_id = ?", (thesis_id,)
    ).fetchone()[0]
    assert n_positions == 0
    fb_notes = db.execute(
        "SELECT notes FROM feedback WHERE id = ?", (event.feedback_id,)
    ).fetchone()[0]
    assert fb_notes == "not in budget today"


def test_record_skip_unknown_thesis_raises(db):
    with pytest.raises(FeedbackError, match="no thesis"):
        record_skip(db, thesis_id=999)


def test_record_sell_by_position_id_closes_position(db, tmp_path):
    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    fill = record_fill(db, thesis_id=thesis_id, price=100.0)
    event = record_sell(
        db, price=120.0, position_id=fill.position_id, reported_at="2026-05-20T00:00:00"
    )
    assert event.action == "sell"
    assert event.position_id == fill.position_id
    assert event.thesis_id == thesis_id
    pos = db.execute(
        "SELECT exit_price, closed_at, status FROM positions WHERE id = ?",
        (fill.position_id,),
    ).fetchone()
    assert pos == (120.0, "2026-05-20T00:00:00", "closed")


def test_record_sell_by_thesis_id_closes_position(db, tmp_path):
    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    fill = record_fill(db, thesis_id=thesis_id, price=100.0)
    event = record_sell(db, price=90.0, thesis_id=thesis_id)
    assert event.position_id == fill.position_id
    status = db.execute(
        "SELECT status FROM positions WHERE id = ?", (fill.position_id,)
    ).fetchone()[0]
    assert status == "closed"


def test_record_sell_requires_target(db):
    with pytest.raises(FeedbackError, match="position_id or thesis_id"):
        record_sell(db, price=100.0)


def test_record_sell_unknown_position_raises(db):
    with pytest.raises(FeedbackError, match="no open position with id="):
        record_sell(db, price=100.0, position_id=999)


def test_record_sell_thesis_without_open_position_raises(db, tmp_path):
    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    with pytest.raises(FeedbackError, match="no open position for thesis"):
        record_sell(db, price=100.0, thesis_id=thesis_id)


def test_record_sell_rejects_already_closed_position(db, tmp_path):
    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    fill = record_fill(db, thesis_id=thesis_id, price=100.0)
    record_sell(db, price=110.0, position_id=fill.position_id)
    with pytest.raises(FeedbackError, match="no open position"):
        record_sell(db, price=120.0, position_id=fill.position_id)


def test_fill_then_sell_then_fill_again_is_allowed(db, tmp_path):
    """After selling, the user can re-enter on the same thesis."""
    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    first = record_fill(db, thesis_id=thesis_id, price=100.0)
    record_sell(db, price=110.0, position_id=first.position_id)
    second = record_fill(db, thesis_id=thesis_id, price=108.0)
    assert second.position_id != first.position_id
    n_open = db.execute(
        "SELECT COUNT(*) FROM positions WHERE thesis_id = ? AND status = 'open'",
        (thesis_id,),
    ).fetchone()[0]
    assert n_open == 1


def test_feedback_flow_feeds_reviewer(db, tmp_path):
    """End-to-end: fill → sell → Reviewer writes a post-mortem."""
    from traders.reviewer import run as reviewer_run

    thesis_id, _ = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    fill = record_fill(db, thesis_id=thesis_id, price=100.0)
    record_sell(
        db, price=120.0, position_id=fill.position_id, reported_at="2026-05-20T00:00:00"
    )
    run_id, n = reviewer_run(db)
    assert n == 1
    outcome = db.execute("SELECT outcome FROM post_mortems").fetchone()[0]
    assert "AAA" in outcome
    assert "+20.00%" in outcome
