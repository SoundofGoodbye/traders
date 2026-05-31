"""Portfolio Manager agent — final filter over the day's theses.

Reads theses from the latest Analyst run (or a specified run), checks them
against open positions for concentration / correlation, and emits a daily
report describing which theses were accepted (forwarded to the user) and
which were rejected (and why). Per-thesis decisions are persisted to
`pm_decisions`. Markdown rendering lives in `traders.reports`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from traders.parameters import LearnedParameters, load_parameters

DEFAULT_MAX_TOTAL_SIZE_PCT = 20.0


@dataclass(frozen=True)
class ThesisRow:
    """The subset of `theses` columns the PM evaluates against."""

    thesis_id: int
    ticker: str
    thesis_type: str
    direction: str
    conviction: int
    suggested_size_pct: float


@dataclass(frozen=True)
class OpenPosition:
    """The subset of `positions` columns the PM evaluates against."""

    ticker: str
    size_pct: float


@dataclass(frozen=True)
class ReportItem:
    """One line in the daily report — accepted or rejected, with reason."""

    thesis_id: int
    ticker: str
    thesis_type: str
    direction: str
    conviction: int
    suggested_size_pct: float
    decision: str
    reason: str


@dataclass(frozen=True)
class DailyReport:
    """The PM's daily output, before rendering."""

    pm_run_id: int
    analyst_run_id: int
    accepted: list[ReportItem]
    rejected: list[ReportItem]


def _latest_analyst_run_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(run_id) FROM theses").fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _theses_for_run(conn: sqlite3.Connection, analyst_run_id: int) -> list[ThesisRow]:
    rows = conn.execute(
        "SELECT id, ticker, thesis_type, direction, conviction, suggested_size_pct"
        " FROM theses WHERE run_id = ? ORDER BY id",
        (analyst_run_id,),
    ).fetchall()
    return [
        ThesisRow(
            thesis_id=int(r[0]),
            ticker=r[1],
            thesis_type=r[2],
            direction=r[3],
            conviction=int(r[4]),
            suggested_size_pct=float(r[5]),
        )
        for r in rows
    ]


def _open_positions(conn: sqlite3.Connection) -> list[OpenPosition]:
    rows = conn.execute(
        "SELECT ticker, size_pct FROM positions WHERE status = 'open'"
    ).fetchall()
    return [OpenPosition(ticker=r[0], size_pct=float(r[1])) for r in rows]


def _next_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(pm_run_id), 0) FROM pm_decisions"
    ).fetchone()
    return int(row[0]) + 1


def _item(t: ThesisRow, decision: str, reason: str) -> ReportItem:
    return ReportItem(
        thesis_id=t.thesis_id,
        ticker=t.ticker,
        thesis_type=t.thesis_type,
        direction=t.direction,
        conviction=t.conviction,
        suggested_size_pct=t.suggested_size_pct,
        decision=decision,
        reason=reason,
    )


def evaluate(
    theses: list[ThesisRow],
    open_positions: list[OpenPosition],
    max_total_size_pct: float = DEFAULT_MAX_TOTAL_SIZE_PCT,
) -> tuple[list[ReportItem], list[ReportItem]]:
    """Apply concentration / correlation checks. Returns (accepted, rejected).

    Rules:
      1. For multiple theses on the same ticker in this run, keep the one
         with the highest conviction (tiebreak: earlier thesis_id wins).
      2. If a ticker already has an open position, reject the thesis. v1
         treats any open position as a block regardless of direction;
         direction-aware hedging logic is a future refinement.
      3. If accepting a thesis would push projected total exposure
         (open positions + already-accepted theses) above
         `max_total_size_pct`, reject. Surviving theses are evaluated in
         descending conviction order so the strongest ideas fit first.
    """
    selected: dict[str, ThesisRow] = {}
    rejected: list[ReportItem] = []
    for t in theses:
        existing = selected.get(t.ticker)
        if existing is None:
            selected[t.ticker] = t
            continue
        if t.conviction > existing.conviction:
            rejected.append(
                _item(
                    existing,
                    "rejected",
                    f"duplicate ticker; preferred thesis {t.thesis_id} "
                    "(higher conviction)",
                )
            )
            selected[t.ticker] = t
        else:
            rejected.append(
                _item(
                    t,
                    "rejected",
                    f"duplicate ticker; kept thesis {existing.thesis_id} "
                    "(higher conviction)",
                )
            )

    held = {p.ticker: p.size_pct for p in open_positions}
    current_exposure = sum(held.values())
    accepted: list[ReportItem] = []
    running = 0.0
    order = sorted(selected.values(), key=lambda x: (-x.conviction, x.thesis_id))
    for t in order:
        if t.ticker in held:
            rejected.append(
                _item(
                    t,
                    "rejected",
                    f"concentration: existing open position in {t.ticker} "
                    f"({held[t.ticker]:.1f}%)",
                )
            )
            continue
        projected = current_exposure + running + t.suggested_size_pct
        if projected > max_total_size_pct:
            rejected.append(
                _item(
                    t,
                    "rejected",
                    f"concentration: total exposure would exceed cap "
                    f"({projected:.1f}% > {max_total_size_pct:.1f}%)",
                )
            )
            continue
        accepted.append(
            _item(
                t,
                "accepted",
                f"{t.thesis_type} thesis, conviction {t.conviction}, "
                f"size {t.suggested_size_pct:.1f}%",
            )
        )
        running += t.suggested_size_pct
    return accepted, rejected


def run(
    conn: sqlite3.Connection,
    analyst_run_id: int | None = None,
    max_total_size_pct: float | None = None,
    params: LearnedParameters | None = None,
) -> DailyReport:
    """Run the Portfolio Manager.

    Returns a `DailyReport`. If there is no analyst run yet, the report
    has `pm_run_id == 0` and empty lists; no rows are written.

    ``max_total_size_pct`` overrides the learned parameter when given;
    otherwise it comes from the active `LearnedParameters`.
    """
    cap = (
        max_total_size_pct
        if max_total_size_pct is not None
        else (params or load_parameters()).max_total_size_pct
    )
    target = (
        analyst_run_id
        if analyst_run_id is not None
        else _latest_analyst_run_id(conn)
    )
    if target is None:
        return DailyReport(
            pm_run_id=0, analyst_run_id=0, accepted=[], rejected=[]
        )
    theses = _theses_for_run(conn, target)
    positions = _open_positions(conn)
    pm_run_id = _next_run_id(conn)
    accepted, rejected = evaluate(theses, positions, cap)
    items = [*accepted, *rejected]
    if items:
        created_at = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            "INSERT INTO pm_decisions"
            " (pm_run_id, thesis_id, decision, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (pm_run_id, i.thesis_id, i.decision, i.reason, created_at)
                for i in items
            ],
        )
        conn.commit()
    return DailyReport(
        pm_run_id=pm_run_id,
        analyst_run_id=target,
        accepted=accepted,
        rejected=rejected,
    )
