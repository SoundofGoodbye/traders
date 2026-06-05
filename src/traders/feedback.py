"""Feedback loop — the user reports back what they actually did.

After the Portfolio Manager emits the daily report, the user executes
manually and reports the result here. Each call writes a row to
`feedback` and, where applicable, opens or closes a row in `positions`.
Slice 5's Reviewer walks `positions` once they're closed, so this is
the path that wires the daily output back into the weekly review.

Four actions:

* `fill`    — full fill at the suggested size; opens a new position.
* `partial` — partial fill at an explicit smaller size; opens a position.
* `skip`    — user declined the suggestion; log only, no position change.
* `sell`    — user closed an existing open position; sets exit_price and
              flips the position to `closed`.

`theses.status` is intentionally untouched — no other agent reads it,
and positions + feedback are the source of truth for the lifecycle.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

ACTIONS = ("fill", "partial", "skip", "sell")

# A position can never exceed 100% of NAV; sizes outside (0, 100] are rejected at
# every write boundary (audit H1) so the exposure/concentration math and the PM's
# size cap stay meaningful.
_MAX_SIZE_PCT = 100.0


class FeedbackError(ValueError):
    """Raised when the requested feedback can't be reconciled with state."""


@dataclass(frozen=True)
class FeedbackEvent:
    """A feedback row as it was persisted, with its position link if any."""

    feedback_id: int
    thesis_id: int
    action: str
    position_id: int | None
    price: float | None
    size_pct: float | None
    notes: str | None
    reported_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_open(price: float, size_pct: float) -> None:
    """Enforce the position invariants the rest of the system assumes (audit H1).

    Every path that opens a position funnels through here. A non-positive price
    corrupts PnL (and can flip a loss to a gain in ``compute_pnl_pct``); a size
    outside ``(0, 100]`` poisons the exposure/concentration math and silently
    bypasses the PM's size cap. Fail loud at the boundary instead.
    """
    if not price > 0:
        raise FeedbackError(f"price must be > 0 (got {price})")
    if not 0 < size_pct <= _MAX_SIZE_PCT:
        raise FeedbackError(f"size_pct must be in (0, {_MAX_SIZE_PCT:g}] (got {size_pct})")


def _thesis_row(conn: sqlite3.Connection, thesis_id: int) -> tuple[str, float]:
    row = conn.execute(
        "SELECT ticker, suggested_size_pct FROM theses WHERE id = ?",
        (thesis_id,),
    ).fetchone()
    if row is None:
        raise FeedbackError(f"no thesis with id={thesis_id}")
    return row[0], float(row[1])


def _open_position_for_thesis(conn: sqlite3.Connection, thesis_id: int) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT id, ticker FROM positions WHERE thesis_id = ? AND status = 'open'",
        (thesis_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row[0]), row[1]


def _open_position_by_id(conn: sqlite3.Connection, position_id: int) -> tuple[int, int, str]:
    row = conn.execute(
        "SELECT id, thesis_id, ticker FROM positions WHERE id = ? AND status = 'open'",
        (position_id,),
    ).fetchone()
    if row is None:
        raise FeedbackError(f"no open position with id={position_id}")
    return int(row[0]), int(row[1]), row[2]


def _insert_feedback(
    conn: sqlite3.Connection,
    *,
    thesis_id: int,
    action: str,
    position_id: int | None,
    price: float | None,
    size_pct: float | None,
    notes: str | None,
    reported_at: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO feedback"
        " (thesis_id, action, notes, reported_at, price, size_pct, position_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (thesis_id, action, notes, reported_at, price, size_pct, position_id),
    )
    return int(cur.lastrowid)


def _open_position(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    thesis_id: int,
    price: float,
    size_pct: float,
    opened_at: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, entry_price, size_pct, status)"
        " VALUES (?, ?, ?, ?, ?, 'open')",
        (ticker, thesis_id, opened_at, price, size_pct),
    )
    return int(cur.lastrowid)


def _close_position(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    price: float,
    closed_at: str,
) -> None:
    conn.execute(
        "UPDATE positions SET status = 'closed', exit_price = ?, closed_at = ? WHERE id = ?",
        (price, closed_at, position_id),
    )


def record_fill(
    conn: sqlite3.Connection,
    thesis_id: int,
    price: float,
    size_pct: float | None = None,
    notes: str | None = None,
    reported_at: str | None = None,
) -> FeedbackEvent:
    """Record a full fill: open a position at the thesis's suggested size.

    `size_pct` defaults to the thesis's `suggested_size_pct`. Pass an
    override only if the user filled at a different size than suggested
    but considers it a full execution (rare; usually use `partial`).
    """
    ticker, suggested = _thesis_row(conn, thesis_id)
    size = float(size_pct) if size_pct is not None else suggested
    _validate_open(float(price), size)
    if _open_position_for_thesis(conn, thesis_id) is not None:
        raise FeedbackError(f"thesis {thesis_id} already has an open position; sell it first")
    ts = reported_at or _now()
    position_id = _open_position(
        conn,
        ticker=ticker,
        thesis_id=thesis_id,
        price=float(price),
        size_pct=size,
        opened_at=ts,
    )
    feedback_id = _insert_feedback(
        conn,
        thesis_id=thesis_id,
        action="fill",
        position_id=position_id,
        price=float(price),
        size_pct=size,
        notes=notes,
        reported_at=ts,
    )
    conn.commit()
    return FeedbackEvent(
        feedback_id=feedback_id,
        thesis_id=thesis_id,
        action="fill",
        position_id=position_id,
        price=float(price),
        size_pct=size,
        notes=notes,
        reported_at=ts,
    )


def record_partial(
    conn: sqlite3.Connection,
    thesis_id: int,
    price: float,
    size_pct: float,
    notes: str | None = None,
    reported_at: str | None = None,
) -> FeedbackEvent:
    """Record a partial fill at an explicit smaller size.

    `size_pct` is required — a partial without a size is just a fill.
    """
    ticker, _suggested = _thesis_row(conn, thesis_id)
    _validate_open(float(price), float(size_pct))
    if _open_position_for_thesis(conn, thesis_id) is not None:
        raise FeedbackError(f"thesis {thesis_id} already has an open position; sell it first")
    ts = reported_at or _now()
    position_id = _open_position(
        conn,
        ticker=ticker,
        thesis_id=thesis_id,
        price=float(price),
        size_pct=float(size_pct),
        opened_at=ts,
    )
    feedback_id = _insert_feedback(
        conn,
        thesis_id=thesis_id,
        action="partial",
        position_id=position_id,
        price=float(price),
        size_pct=float(size_pct),
        notes=notes,
        reported_at=ts,
    )
    conn.commit()
    return FeedbackEvent(
        feedback_id=feedback_id,
        thesis_id=thesis_id,
        action="partial",
        position_id=position_id,
        price=float(price),
        size_pct=float(size_pct),
        notes=notes,
        reported_at=ts,
    )


def record_skip(
    conn: sqlite3.Connection,
    thesis_id: int,
    notes: str | None = None,
    reported_at: str | None = None,
) -> FeedbackEvent:
    """Record that the user declined the suggested thesis. Log only."""
    _thesis_row(conn, thesis_id)
    if _open_position_for_thesis(conn, thesis_id) is not None:
        raise FeedbackError(f"thesis {thesis_id} has an open position; sell it before skipping")
    ts = reported_at or _now()
    feedback_id = _insert_feedback(
        conn,
        thesis_id=thesis_id,
        action="skip",
        position_id=None,
        price=None,
        size_pct=None,
        notes=notes,
        reported_at=ts,
    )
    conn.commit()
    return FeedbackEvent(
        feedback_id=feedback_id,
        thesis_id=thesis_id,
        action="skip",
        position_id=None,
        price=None,
        size_pct=None,
        notes=notes,
        reported_at=ts,
    )


def record_sell(
    conn: sqlite3.Connection,
    price: float,
    *,
    position_id: int | None = None,
    thesis_id: int | None = None,
    notes: str | None = None,
    reported_at: str | None = None,
) -> FeedbackEvent:
    """Close an existing open position. Identify it by id or by thesis."""
    if position_id is None and thesis_id is None:
        raise FeedbackError("record_sell requires position_id or thesis_id")
    if position_id is not None:
        pid, tid, _ticker = _open_position_by_id(conn, position_id)
    else:
        assert thesis_id is not None
        _thesis_row(conn, thesis_id)
        found = _open_position_for_thesis(conn, thesis_id)
        if found is None:
            raise FeedbackError(f"no open position for thesis {thesis_id}")
        pid = found[0]
        tid = thesis_id
    ts = reported_at or _now()
    _close_position(conn, position_id=pid, price=float(price), closed_at=ts)
    feedback_id = _insert_feedback(
        conn,
        thesis_id=tid,
        action="sell",
        position_id=pid,
        price=float(price),
        size_pct=None,
        notes=notes,
        reported_at=ts,
    )
    conn.commit()
    return FeedbackEvent(
        feedback_id=feedback_id,
        thesis_id=tid,
        action="sell",
        position_id=pid,
        price=float(price),
        size_pct=None,
        notes=notes,
        reported_at=ts,
    )
