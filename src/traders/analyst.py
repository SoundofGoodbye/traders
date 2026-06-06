"""Analyst agent — turns research notes into theses.

Reads research notes from the latest Researcher run (or a specified
run) and, for each note, asks a `ThesisGenerator` for zero-or-more
draft theses. Each draft is persisted as a row in `theses` with status
`open`. Real model-driven generation lands in a later slice; v1 ships
the stub.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from traders.db import immediate
from traders.signals import (
    DIRECTIONS,
    THESIS_TYPES,
    DraftThesis,
    StubThesisGenerator,
    ThesisGenerator,
)

logger = logging.getLogger(__name__)

# Defense in depth (audit H1): the Analyst is the single persistence boundary for
# generator output. The LLM generator clamps its own output, but the
# ThesisGenerator protocol does not enforce ranges — so an out-of-contract draft
# is dropped here rather than written to `theses`, where the PM and exposure math
# would trust it blindly.
_MAX_SIZE_PCT = 100.0


def _is_valid_draft(draft: object) -> bool:
    try:
        return (
            draft.thesis_type in THESIS_TYPES
            and draft.direction in DIRECTIONS
            and 1 <= draft.conviction <= 5
            and 0 < draft.suggested_size_pct <= _MAX_SIZE_PCT
        )
    except TypeError:
        return False


def _latest_research_run_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(run_id) FROM research_notes").fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _notes_for_run(conn: sqlite3.Connection, research_run_id: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT ticker, content FROM research_notes WHERE run_id = ? ORDER BY id",
        (research_run_id,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _next_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(run_id), 0) FROM theses").fetchone()
    return int(row[0]) + 1


def run(
    conn: sqlite3.Connection,
    generator: ThesisGenerator | None = None,
    research_run_id: int | None = None,
) -> tuple[int, int]:
    """Run the Analyst.

    Returns `(run_id, theses_written)`. Returns `(0, 0)` if there is
    no research run to read from yet.
    """
    gen: ThesisGenerator = generator or StubThesisGenerator()
    target = research_run_id if research_run_id is not None else _latest_research_run_id(conn)
    if target is None:
        return 0, 0
    notes = _notes_for_run(conn, target)
    if not notes:
        return _next_run_id(conn), 0
    created_at = datetime.now(timezone.utc).isoformat()
    # Generate drafts (the LLM generator does network I/O) before taking the write
    # lock; allocate the run id and insert atomically (audit L4).
    drafts: list[tuple[str, DraftThesis]] = []
    for ticker, content in notes:
        for draft in gen.generate(ticker, content):
            if not _is_valid_draft(draft):
                logger.warning("Analyst: dropping out-of-contract draft for %s: %r", ticker, draft)
                continue
            drafts.append((ticker, draft))
    with immediate(conn):
        run_id = _next_run_id(conn)
        rows = [
            (
                ticker,
                draft.thesis_type,
                draft.direction,
                draft.conviction,
                draft.suggested_size_pct,
                draft.exit_condition,
                draft.rationale,
                created_at,
                run_id,
                target,
            )
            for ticker, draft in drafts
        ]
        if rows:
            conn.executemany(
                "INSERT INTO theses ("
                "ticker, thesis_type, direction, conviction, suggested_size_pct,"
                " exit_condition, rationale, created_at, run_id, research_run_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
    return run_id, len(rows)
