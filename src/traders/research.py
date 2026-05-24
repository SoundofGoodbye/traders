"""Researcher agent — per-candidate deep dive.

Reads candidates from the latest Scout run (or a specified run), pulls
structured evidence via a `DataSource`, and writes one row per
candidate to `research_notes`. The note's `content` is a human-readable
markdown digest; `sources` is a JSON array of citable references so
later agents (and the Reviewer) can audit claims back to data.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from traders.data_sources import DataPoint, DataSource, StubDataSource

_KIND_ORDER = ("fundamentals", "filing", "news")


def _latest_scout_run_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(scout_run_id) FROM candidates").fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _candidates_for_run(conn: sqlite3.Connection, scout_run_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT ticker FROM candidates WHERE scout_run_id = ? ORDER BY id",
        (scout_run_id,),
    ).fetchall()
    return [r[0] for r in rows]


def _next_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(run_id), 0) FROM research_notes").fetchone()
    return int(row[0]) + 1


def render_content(ticker: str, points: list[DataPoint]) -> str:
    """Compose a markdown-ish digest from the raw data points."""
    if not points:
        return f"# {ticker}\n\n(no data available)"
    sections: dict[str, list[DataPoint]] = {}
    for p in points:
        sections.setdefault(p.kind, []).append(p)
    lines = [f"# {ticker}"]
    for kind in _KIND_ORDER:
        items = sections.pop(kind, [])
        if not items:
            continue
        lines.append("")
        lines.append(f"## {kind}")
        for p in items:
            lines.append(f"- **{p.title}** ({p.published_at}): {p.snippet}")
    for kind, items in sections.items():
        lines.append("")
        lines.append(f"## {kind}")
        for p in items:
            lines.append(f"- **{p.title}** ({p.published_at}): {p.snippet}")
    return "\n".join(lines)


def render_sources(points: list[DataPoint]) -> str:
    """JSON-encode the source list for the `sources` column."""
    return json.dumps(
        [
            {
                "kind": p.kind,
                "title": p.title,
                "url": p.url,
                "published_at": p.published_at,
            }
            for p in points
        ]
    )


def run(
    conn: sqlite3.Connection,
    data_source: DataSource | None = None,
    scout_run_id: int | None = None,
) -> tuple[int, list[str]]:
    """Run the Researcher.

    Returns `(run_id, tickers_written)`. Returns `(0, [])` if there is
    no scout run to read from yet.
    """
    ds: DataSource = data_source or StubDataSource()
    target = scout_run_id if scout_run_id is not None else _latest_scout_run_id(conn)
    if target is None:
        return 0, []
    tickers = _candidates_for_run(conn, target)
    run_id = _next_run_id(conn)
    if not tickers:
        return run_id, []
    created_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for t in tickers:
        points = list(ds.fetch(t))
        rows.append(
            (
                t,
                run_id,
                render_content(t, points),
                render_sources(points),
                created_at,
            )
        )
    conn.executemany(
        "INSERT INTO research_notes (ticker, run_id, content, sources, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return run_id, tickers
