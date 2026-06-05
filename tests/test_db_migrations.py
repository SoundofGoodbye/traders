"""Migration applier mechanics: the open-position uniqueness backstop (M3) and
atomic apply (M4)."""

import sqlite3
from pathlib import Path

import pytest

from traders.db import apply_migrations, connect

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _seed_open_thesis(conn: sqlite3.Connection, ticker: str = "AAA") -> int:
    cur = conn.execute(
        "INSERT INTO theses (ticker, thesis_type, direction, conviction,"
        " suggested_size_pct, created_at, status)"
        " VALUES (?, 'value', 'long', 3, 2.0, '2026-01-01', 'open')",
        (ticker,),
    )
    return int(cur.lastrowid)


def _open_position(
    conn: sqlite3.Connection, thesis_id: int, opened_at: str = "2026-01-01", status: str = "open"
) -> None:
    conn.execute(
        "INSERT INTO positions (ticker, thesis_id, opened_at, entry_price, size_pct, status)"
        " VALUES ('AAA', ?, ?, 100.0, 2.0, ?)",
        (thesis_id, opened_at, status),
    )


def test_one_open_position_per_thesis_is_enforced(tmp_path):
    conn = connect(tmp_path / "t.db")
    apply_migrations(conn, MIGRATIONS)
    tid = _seed_open_thesis(conn)
    _open_position(conn, tid)
    with pytest.raises(sqlite3.IntegrityError):
        _open_position(conn, tid, opened_at="2026-01-02")
    conn.close()


def test_reentry_after_close_is_allowed(tmp_path):
    conn = connect(tmp_path / "t.db")
    apply_migrations(conn, MIGRATIONS)
    tid = _seed_open_thesis(conn)
    _open_position(conn, tid, status="closed")
    _open_position(conn, tid, status="closed")
    _open_position(conn, tid, status="open")  # one open alongside closed history is fine
    n = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE thesis_id = ? AND status = 'open'", (tid,)
    ).fetchone()[0]
    assert n == 1
    conn.close()


def test_failed_migration_is_atomic(tmp_path):
    """A migration that fails partway leaves no half-applied schema and no
    recorded version (audit M4)."""
    mdir = tmp_path / "migs"
    mdir.mkdir()
    (mdir / "001_ok.sql").write_text(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);\n"
        "CREATE TABLE a (id INTEGER);"
    )
    # 002: first statement valid, second a syntax error -> whole migration rolls back.
    (mdir / "002_bad.sql").write_text(
        "CREATE TABLE b (id INTEGER);\nCREATE TABLE b_oops (id INTEGER) NOPE;"
    )
    conn = connect(tmp_path / "t.db")
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(conn, mdir)
    versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert versions == {1}  # 001 recorded; 002 not
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "a" in tables  # 001 succeeded
    assert "b" not in tables  # 002 rolled back fully, not left half-applied
    conn.close()
