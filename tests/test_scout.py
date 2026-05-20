import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from traders.db import apply_migrations
from traders.scout import filter_candidates, run

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_filter_is_deterministic():
    wl = [f"T{i}" for i in range(30)]
    a = filter_candidates(wl, date(2026, 5, 20), batch_size=5)
    b = filter_candidates(wl, date(2026, 5, 20), batch_size=5)
    assert a == b
    assert len(a) == 5


def test_filter_rotates_across_days():
    wl = [f"T{i}" for i in range(30)]
    a = filter_candidates(wl, date(2026, 5, 20), batch_size=5)
    b = filter_candidates(wl, date(2026, 5, 21), batch_size=5)
    assert a != b


def test_filter_empty_watchlist():
    assert filter_candidates([], date(2026, 5, 20), batch_size=5) == []


def test_filter_wraps_around():
    wl = ["A", "B", "C", "D", "E"]
    # ordinal=1, size=3, start = (1*3) % 5 = 3 → wraps
    picks = filter_candidates(wl, date.fromordinal(1), batch_size=3)
    assert picks == ["D", "E", "A"]


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_migrations(conn, MIGRATIONS)
    yield conn
    conn.close()


def test_run_writes_candidates(db, tmp_path):
    wl_path = tmp_path / "wl.json"
    wl_path.write_text(json.dumps({"sp100": ["AAA", "BBB", "CCC", "DDD"], "eurostoxx50": []}))
    run_id, picks = run(db, watchlist_path=wl_path, run_date=date(2026, 5, 20), batch_size=2)
    assert run_id == 1
    assert len(picks) == 2
    rows = db.execute(
        "SELECT ticker, scout_run_id, reason FROM candidates ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert {r[0] for r in rows} == set(picks)
    assert all(r[1] == run_id for r in rows)
    assert all(r[2] for r in rows)


def test_run_increments_run_id(db, tmp_path):
    wl_path = tmp_path / "wl.json"
    wl_path.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    r1, _ = run(db, watchlist_path=wl_path, run_date=date(2026, 5, 20), batch_size=2)
    r2, _ = run(db, watchlist_path=wl_path, run_date=date(2026, 5, 21), batch_size=2)
