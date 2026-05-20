"""SQLite connection + minimal forward-only migration applier."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path("data") / "traders.db"
DEFAULT_MIGRATIONS_DIR = Path("migrations")


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (and create-if-missing) the SQLite database."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def apply_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path | str | None = None,
) -> list[int]:
    """Apply any new `migrations/NNN_*.sql` in order; return versions applied."""
    mdir = Path(migrations_dir) if migrations_dir else DEFAULT_MIGRATIONS_DIR
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone() is not None
    applied: set[int] = set()
    if has_table:
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    new: list[int] = []
    for sql_file in sorted(mdir.glob("*.sql")):
        version = int(sql_file.name.split("_", 1)[0])
        if version in applied:
            continue
        conn.executescript(sql_file.read_text())
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        new.append(version)
    return new
