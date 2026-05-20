"""Scout agent — filters the watchlist into candidates per run.

Deterministic at this slice: no LLM, no external data. The heuristic
is a date-seeded rotation over the watchlist so each daily run picks
a small subset and coverage cycles through the full list over time.
Real data-driven filtering arrives once slice 7+ adds data sources.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_WATCHLIST = Path(__file__).parent / "data" / "watchlist.json"
DEFAULT_BATCH_SIZE = 10


def load_watchlist(path: Path | None = None) -> list[str]:
    """Load the watchlist JSON and return a flat, de-duplicated ticker list."""
    raw = json.loads(Path(path or DEFAULT_WATCHLIST).read_text())
    tickers: list[str] = []
    for bucket in ("sp100", "eurostoxx50"):
        tickers.extend(raw.get(bucket, []))
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def filter_candidates(
    watchlist: list[str],
    run_date: date,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[str]:
    """Pick a rotating window from the watchlist.

    Placeholder heuristic until real data lands. Stable for a given
    (watchlist, date, batch_size); rotates across days.
    """
    if not watchlist:
        return []
    n = len(watchlist)
    size = min(batch_size, n)
    start = (run_date.toordinal() * size) % n
    if start + size <= n:
        return watchlist[start : start + size]
    return watchlist[start:] + watchlist[: (start + size) - n]


def _next_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(scout_run_id), 0) FROM candidates").fetchone()
    return int(row[0]) + 1


def run(
    conn: sqlite3.Connection,
    watchlist_path: Path | None = None,
    run_date: date | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, list[str]]:
    """Run the Scout: load watchlist, filter, write to `candidates`."""
    watchlist = load_watchlist(watchlist_path)
    rd = run_date or datetime.now(timezone.utc).date()
    picks = filter_candidates(watchlist, rd, batch_size)
    run_id = _next_run_id(conn)
    created_at = datetime.now(timezone.utc).isoformat()
    reason = f"rotation window for {rd.isoformat()}"
    conn.executemany(
        "INSERT INTO candidates (ticker, scout_run_id, reason, created_at)"
        " VALUES (?, ?, ?, ?)",
        [(t, run_id, reason, created_at) for t in picks],
    )
    conn.commit()
    return run_id, picks
