import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from traders.analyst import run as analyst_run
from traders.db import apply_migrations
from traders.post_mortems import PostMortemDraft
from traders.research import run as research_run
from traders.reviewer import run as reviewer_run
from traders.scout import run as scout_run

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeGenerator:
    def __init__(self, outcome="stub-outcome", lessons="stub-lessons"):
        self.calls: list[tuple] = []
        self.outcome = outcome
        self.lessons = lessons

    def generate(self, position, thesis):
        self.calls.append((position, thesis))
        return PostMortemDraft(outcome=self.outcome, lessons=self.lessons)


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
        "SELECT id FROM theses WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return int(row[0])


def _insert_closed_position(
    db,
    ticker,
    thesis_id,
    entry=100.0,
    exit_=110.0,
    size=2.0,
    opened_at="2026-05-01",
    closed_at="2026-05-20",
):
    cur = db.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, closed_at, entry_price, exit_price,"
        " size_pct, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'closed')",
        (ticker, thesis_id, opened_at, closed_at, entry, exit_, size),
    )
    db.commit()
    return cur.lastrowid


def test_run_writes_one_post_mortem_per_unreviewed_closed_position(db, tmp_path):
    t1 = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    t2 = _seed_thesis(db, tmp_path, "BBB", date(2026, 5, 2))
    p1 = _insert_closed_position(db, "AAA", t1)
    p2 = _insert_closed_position(db, "BBB", t2, entry=50.0, exit_=40.0)
    gen = FakeGenerator()
    run_id, n = reviewer_run(db, generator=gen)
    assert run_id == 1
    assert n == 2
    rows = db.execute(
        "SELECT position_id, reviewer_run_id, outcome, lessons"
        " FROM post_mortems ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == [p1, p2]
    for _, r_id, outcome, lessons in rows:
        assert r_id == 1
        assert outcome == "stub-outcome"
        assert lessons == "stub-lessons"
    assert sorted(c[0].ticker for c in gen.calls) == ["AAA", "BBB"]


def test_run_skips_already_reviewed_positions(db, tmp_path):
    t1 = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    _insert_closed_position(db, "AAA", t1)
    first_id, first_n = reviewer_run(db)
    assert first_n == 1
    second_id, second_n = reviewer_run(db)
    assert second_n == 0
    assert second_id == first_id + 1
    total = db.execute("SELECT COUNT(*) FROM post_mortems").fetchone()[0]
    assert total == 1


def test_run_ignores_open_positions(db, tmp_path):
    t1 = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    db.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, size_pct, status)"
        " VALUES ('AAA', ?, '2026-05-01', 2.0, 'open')",
        (t1,),
    )
    db.commit()
    run_id, n = reviewer_run(db)
    assert run_id == 0
    assert n == 0
    total = db.execute("SELECT COUNT(*) FROM post_mortems").fetchone()[0]
    assert total == 0


def test_run_with_no_positions_returns_zero(db):
    run_id, n = reviewer_run(db)
    assert run_id == 0
    assert n == 0


def test_run_passes_position_and_thesis_to_generator(db, tmp_path):
    t1 = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    p1 = _insert_closed_position(db, "AAA", t1, entry=100.0, exit_=120.0)
    gen = FakeGenerator()
    reviewer_run(db, generator=gen)
    assert len(gen.calls) == 1
    position, thesis = gen.calls[0]
    assert position.position_id == p1
    assert position.ticker == "AAA"
    assert position.entry_price == 100.0
    assert position.exit_price == 120.0
    assert position.size_pct == 2.0
    assert thesis.thesis_id == t1
    assert thesis.direction in ("long", "short")
    assert thesis.thesis_type in ("value", "catalyst", "momentum", "mean-reversion")


def test_run_uses_stub_generator_by_default(db, tmp_path):
    t1 = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    _insert_closed_position(db, "AAA", t1, entry=100.0, exit_=120.0)
    run_id, n = reviewer_run(db)
    assert run_id == 1
    assert n == 1
    outcome, lessons = db.execute(
        "SELECT outcome, lessons FROM post_mortems"
    ).fetchone()
    assert "+20.00%" in outcome
    assert "AAA" in outcome
    assert "[stub]" in lessons


def test_run_increments_reviewer_run_id(db, tmp_path):
    t1 = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    t2 = _seed_thesis(db, tmp_path, "BBB", date(2026, 5, 2))
    _insert_closed_position(db, "AAA", t1)
    r1_id, _ = reviewer_run(db)
    _insert_closed_position(db, "BBB", t2)
    r2_id, _ = reviewer_run(db)
    assert r2_id == r1_id + 1


def test_run_handles_position_with_missing_prices(db, tmp_path):
    t1 = _seed_thesis(db, tmp_path, "AAA", date(2026, 5, 1))
    db.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, closed_at, size_pct, status)"
        " VALUES ('AAA', ?, '2026-05-01', '2026-05-20', 2.0, 'closed')",
        (t1,),
    )
    db.commit()
    run_id, n = reviewer_run(db)
    assert n == 1
    outcome = db.execute("SELECT outcome FROM post_mortems").fetchone()[0]
    assert "unknown" in outcome.lower()
