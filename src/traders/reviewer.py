"""Reviewer agent — weekly post-mortem writer.

Walks closed positions that don't yet have a post-mortem, asks a
`PostMortemGenerator` for a draft, and persists one row per position to
`post_mortems`. Lessons are stub text in v1; the path is set up so an
LLM-backed generator can drop in behind the same protocol.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from traders.post_mortems import (
    ClosedPosition,
    PostMortemGenerator,
    StubPostMortemGenerator,
    ThesisContext,
)


def _has_any_closed_positions(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM positions WHERE status = 'closed' LIMIT 1"
    ).fetchone()
    return row is not None


def _next_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(reviewer_run_id), 0) FROM post_mortems"
    ).fetchone()
    return int(row[0]) + 1


def _unreviewed_closed_positions(
    conn: sqlite3.Connection,
) -> list[tuple[ClosedPosition, ThesisContext]]:
    rows = conn.execute(
        "SELECT p.id, p.ticker, p.thesis_id, t.direction, p.opened_at, p.closed_at,"
        " p.entry_price, p.exit_price, p.size_pct,"
        " t.thesis_type, t.conviction, t.suggested_size_pct,"
        " t.exit_condition, t.rationale"
        " FROM positions p"
        " JOIN theses t ON t.id = p.thesis_id"
        " LEFT JOIN post_mortems pm ON pm.position_id = p.id"
        " WHERE p.status = 'closed' AND pm.id IS NULL"
        " ORDER BY p.id"
    ).fetchall()
    out: list[tuple[ClosedPosition, ThesisContext]] = []
    for r in rows:
        position = ClosedPosition(
            position_id=int(r[0]),
            ticker=r[1],
            thesis_id=int(r[2]),
            direction=r[3],
            opened_at=r[4],
            closed_at=r[5],
            entry_price=None if r[6] is None else float(r[6]),
            exit_price=None if r[7] is None else float(r[7]),
            size_pct=float(r[8]),
        )
        thesis = ThesisContext(
            thesis_id=int(r[2]),
            thesis_type=r[9],
            direction=r[3],
            conviction=int(r[10]),
            suggested_size_pct=float(r[11]),
            exit_condition=r[12],
            rationale=r[13],
        )
        out.append((position, thesis))
    return out


def run(
    conn: sqlite3.Connection,
    generator: PostMortemGenerator | None = None,
) -> tuple[int, int]:
    """Run the Reviewer.

    Returns `(reviewer_run_id, post_mortems_written)`. Returns `(0, 0)`
    if there are no closed positions to review.
    """
    gen: PostMortemGenerator = generator or StubPostMortemGenerator()
    if not _has_any_closed_positions(conn):
        return 0, 0
    pairs = _unreviewed_closed_positions(conn)
    run_id = _next_run_id(conn)
    if not pairs:
        return run_id, 0
    created_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for position, thesis in pairs:
        draft = gen.generate(position, thesis)
        rows.append(
            (position.position_id, run_id, draft.outcome, draft.lessons, created_at)
        )
    conn.executemany(
        "INSERT INTO post_mortems"
        " (position_id, reviewer_run_id, outcome, lessons, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return run_id, len(rows)
