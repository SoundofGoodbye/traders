"""Tests for db connection setup and the immediate() write-lock helper (audit L4)."""

import pytest

from traders.db import connect, immediate


def test_connect_sets_busy_timeout(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 1000
    conn.close()


def test_immediate_commits_on_success(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    with immediate(conn):
        conn.execute("INSERT INTO t VALUES (1)")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    conn.close()


def test_immediate_rolls_back_on_error(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    with pytest.raises(ValueError):
        with immediate(conn):
            conn.execute("INSERT INTO t VALUES (1)")
            raise ValueError("boom")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    conn.close()


def test_immediate_serializes_run_id_allocation(tmp_path):
    """A second writer blocks (busy_timeout) until the first commits, so two
    concurrent allocations of MAX(id)+1 don't collide."""
    db = tmp_path / "t.db"
    a = connect(db)
    b = connect(db)
    a.execute("CREATE TABLE runs (id INTEGER)")
    a.commit()

    # A holds the write lock; B's BEGIN IMMEDIATE must wait, not error.
    a.execute("BEGIN IMMEDIATE")
    a.execute("INSERT INTO runs VALUES ((SELECT COALESCE(MAX(id), 0) + 1 FROM runs))")
    a.commit()
    with immediate(b):
        nxt = b.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM runs").fetchone()[0]
        b.execute("INSERT INTO runs VALUES (?)", (nxt,))
    ids = [r[0] for r in a.execute("SELECT id FROM runs ORDER BY id")]
    assert ids == [1, 2]
    a.close()
    b.close()
