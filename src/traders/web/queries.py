"""Read-only query layer for the web UI.

Typed read functions over the same `sqlite3` connection the agents use.
The UI never writes through here — write actions go through
`traders.feedback`. Mirrors the SELECT shapes already used by the agents
(`portfolio.py`, `reports.py`) rather than introducing a new data model.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

_THESIS_COLS = (
    "id, ticker, thesis_type, direction, conviction, suggested_size_pct,"
    " exit_condition, rationale, created_at, status, run_id, research_run_id"
)
# Same columns, qualified for queries that JOIN another table with an `id`.
_THESIS_COLS_Q = "theses." + _THESIS_COLS.replace(", ", ", theses.")


@dataclass(frozen=True)
class Candidate:
    """One Scout candidate row."""

    ticker: str
    reason: str | None
    scout_run_id: int


@dataclass(frozen=True)
class Thesis:
    """A full `theses` row, including rationale / exit / sizing."""

    id: int
    ticker: str
    thesis_type: str
    direction: str
    conviction: int
    suggested_size_pct: float
    exit_condition: str | None
    rationale: str | None
    created_at: str
    status: str
    run_id: int | None
    research_run_id: int | None


@dataclass(frozen=True)
class Pick:
    """A PM decision joined with its thesis."""

    thesis: Thesis
    decision: str
    reason: str | None
    pm_run_id: int


@dataclass(frozen=True)
class ResearchNote:
    """A `research_notes` row with its sources decoded to a list."""

    id: int
    ticker: str
    run_id: int
    content: str
    sources: list[str]
    created_at: str


@dataclass(frozen=True)
class Backlink:
    """A PM decision that surfaced a given thesis."""

    pm_run_id: int
    decision: str
    reason: str | None


@dataclass(frozen=True)
class PostMortem:
    """A `post_mortems` row joined with its position + thesis context."""

    id: int
    reviewer_run_id: int
    position_id: int
    ticker: str
    direction: str
    thesis_type: str
    entry_price: float | None
    exit_price: float | None
    size_pct: float
    opened_at: str
    closed_at: str | None
    outcome: str | None
    lessons: str | None
    created_at: str


@dataclass(frozen=True)
class Position:
    """A `positions` row plus its thesis direction (for P&L)."""

    id: int
    ticker: str
    thesis_id: int
    opened_at: str
    closed_at: str | None
    entry_price: float | None
    exit_price: float | None
    size_pct: float
    status: str
    direction: str | None


def _thesis(row: tuple) -> Thesis:
    return Thesis(
        id=int(row[0]),
        ticker=row[1],
        thesis_type=row[2],
        direction=row[3],
        conviction=int(row[4]),
        suggested_size_pct=float(row[5]),
        exit_condition=row[6],
        rationale=row[7],
        created_at=row[8],
        status=row[9],
        run_id=None if row[10] is None else int(row[10]),
        research_run_id=None if row[11] is None else int(row[11]),
    )


def _position(row: tuple) -> Position:
    return Position(
        id=int(row[0]),
        ticker=row[1],
        thesis_id=int(row[2]),
        opened_at=row[3],
        closed_at=row[4],
        entry_price=None if row[5] is None else float(row[5]),
        exit_price=None if row[6] is None else float(row[6]),
        size_pct=float(row[7]),
        status=row[8],
        direction=row[9],
    )


_POSITION_SELECT = (
    "SELECT p.id, p.ticker, p.thesis_id, p.opened_at, p.closed_at,"
    " p.entry_price, p.exit_price, p.size_pct, p.status, t.direction"
    " FROM positions p LEFT JOIN theses t ON t.id = p.thesis_id"
)


def parse_sources(raw: str | None) -> list[str]:
    """Decode a `research_notes.sources` JSON blob into a flat list.

    Sources are persisted as a JSON array of strings. Returns an empty
    list for null/blank values and a single-item list for anything that
    isn't a JSON array — display code never trusts it.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return [raw]
    if isinstance(data, list):
        return [str(x) for x in data]
    return [str(data)]


# --- candidates -------------------------------------------------------------


def latest_scout_run_id(conn: sqlite3.Connection) -> int | None:
    """Most recent `scout_run_id` in `candidates`, or None if empty."""
    row = conn.execute("SELECT MAX(scout_run_id) FROM candidates").fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def candidates_for_run(conn: sqlite3.Connection, scout_run_id: int) -> list[Candidate]:
    """All candidates from one Scout run, in insertion order."""
    rows = conn.execute(
        "SELECT ticker, reason, scout_run_id FROM candidates WHERE scout_run_id = ? ORDER BY id",
        (scout_run_id,),
    ).fetchall()
    return [Candidate(ticker=r[0], reason=r[1], scout_run_id=int(r[2])) for r in rows]


# --- theses -----------------------------------------------------------------


def latest_analyst_run_id(conn: sqlite3.Connection) -> int | None:
    """Most recent analyst `run_id` in `theses`, or None if empty."""
    row = conn.execute("SELECT MAX(run_id) FROM theses").fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def theses_for_run(conn: sqlite3.Connection, run_id: int) -> list[Thesis]:
    """All theses from one Analyst run, in insertion order."""
    rows = conn.execute(
        f"SELECT {_THESIS_COLS} FROM theses WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return [_thesis(r) for r in rows]


def thesis_by_id(conn: sqlite3.Connection, thesis_id: int) -> Thesis | None:
    """A single thesis, or None if it doesn't exist."""
    row = conn.execute(
        f"SELECT {_THESIS_COLS} FROM theses WHERE id = ?",
        (thesis_id,),
    ).fetchone()
    return None if row is None else _thesis(row)


def list_theses(
    conn: sqlite3.Connection,
    *,
    ticker: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_conviction: int | None = None,
) -> list[Thesis]:
    """Filtered thesis list, newest first.

    All filters are optional and combined with AND. `date_from`/`date_to`
    bound `created_at` (ISO strings sort chronologically). Every value is
    bound as a parameter — no user input is interpolated into SQL.
    """
    clauses: list[str] = []
    params: list[object] = []
    if ticker:
        clauses.append("ticker = ?")
        params.append(ticker.strip().upper())
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        # "~" sorts after any time-of-day suffix, so a bare date stays inclusive
        clauses.append("created_at <= ?")
        params.append(date_to + "~")
    if min_conviction is not None:
        clauses.append("conviction >= ?")
        params.append(min_conviction)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT {_THESIS_COLS} FROM theses{where} ORDER BY id DESC",
        params,
    ).fetchall()
    return [_thesis(r) for r in rows]


def notes_for_thesis(conn: sqlite3.Connection, thesis: Thesis) -> list[ResearchNote]:
    """Research notes behind a thesis, matched on its research run + ticker."""
    if thesis.research_run_id is None:
        return []
    rows = conn.execute(
        "SELECT id, ticker, run_id, content, sources, created_at"
        " FROM research_notes WHERE run_id = ? AND ticker = ? ORDER BY id",
        (thesis.research_run_id, thesis.ticker),
    ).fetchall()
    return [
        ResearchNote(
            id=int(r[0]),
            ticker=r[1],
            run_id=int(r[2]),
            content=r[3],
            sources=parse_sources(r[4]),
            created_at=r[5],
        )
        for r in rows
    ]


def backlinks_for_thesis(conn: sqlite3.Connection, thesis_id: int) -> list[Backlink]:
    """PM runs that evaluated this thesis, newest first."""
    rows = conn.execute(
        "SELECT pm_run_id, decision, reason FROM pm_decisions"
        " WHERE thesis_id = ? ORDER BY pm_run_id DESC",
        (thesis_id,),
    ).fetchall()
    return [Backlink(pm_run_id=int(r[0]), decision=r[1], reason=r[2]) for r in rows]


# --- PM picks ---------------------------------------------------------------


def latest_pm_run_id(conn: sqlite3.Connection) -> int | None:
    """Most recent `pm_run_id` in `pm_decisions`, or None if empty."""
    row = conn.execute("SELECT MAX(pm_run_id) FROM pm_decisions").fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def picks_for_run(conn: sqlite3.Connection, pm_run_id: int) -> list[Pick]:
    """PM decisions for one run, each joined with its full thesis.

    Accepted picks sort first, then by descending conviction.
    """
    rows = conn.execute(
        f"SELECT d.decision, d.reason, d.pm_run_id, {_THESIS_COLS_Q}"
        " FROM pm_decisions d JOIN theses ON theses.id = d.thesis_id"
        " WHERE d.pm_run_id = ?"
        " ORDER BY CASE d.decision WHEN 'accepted' THEN 0 ELSE 1 END,"
        " theses.conviction DESC, theses.id",
        (pm_run_id,),
    ).fetchall()
    return [
        Pick(thesis=_thesis(r[3:]), decision=r[0], reason=r[1], pm_run_id=int(r[2])) for r in rows
    ]


# --- positions --------------------------------------------------------------


def open_positions(conn: sqlite3.Connection) -> list[Position]:
    """All open positions, oldest first."""
    rows = conn.execute(f"{_POSITION_SELECT} WHERE p.status = 'open' ORDER BY p.id").fetchall()
    return [_position(r) for r in rows]


def open_position_for_thesis(conn: sqlite3.Connection, thesis_id: int) -> int | None:
    """The open position id for a thesis, or None. Mirrors feedback's check."""
    row = conn.execute(
        "SELECT id FROM positions WHERE thesis_id = ? AND status = 'open'",
        (thesis_id,),
    ).fetchone()
    return None if row is None else int(row[0])


def recently_closed_positions(conn: sqlite3.Connection, limit: int = 20) -> list[Position]:
    """Recently closed positions, most recently closed first."""
    rows = conn.execute(
        f"{_POSITION_SELECT} WHERE p.status = 'closed'"
        " ORDER BY p.closed_at DESC, p.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_position(r) for r in rows]


# --- reviews ----------------------------------------------------------------


def list_post_mortems(conn: sqlite3.Connection, limit: int = 100) -> list[PostMortem]:
    """Weekly post-mortems joined with position + thesis context, newest first."""
    rows = conn.execute(
        "SELECT pm.id, pm.reviewer_run_id, pm.position_id, p.ticker, t.direction,"
        " t.thesis_type, p.entry_price, p.exit_price, p.size_pct,"
        " p.opened_at, p.closed_at, pm.outcome, pm.lessons, pm.created_at"
        " FROM post_mortems pm"
        " JOIN positions p ON p.id = pm.position_id"
        " JOIN theses t ON t.id = p.thesis_id"
        " ORDER BY pm.reviewer_run_id DESC, pm.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        PostMortem(
            id=int(r[0]),
            reviewer_run_id=int(r[1]),
            position_id=int(r[2]),
            ticker=r[3],
            direction=r[4],
            thesis_type=r[5],
            entry_price=None if r[6] is None else float(r[6]),
            exit_price=None if r[7] is None else float(r[7]),
            size_pct=float(r[8]),
            opened_at=r[9],
            closed_at=r[10],
            outcome=r[11],
            lessons=r[12],
            created_at=r[13],
        )
        for r in rows
    ]
