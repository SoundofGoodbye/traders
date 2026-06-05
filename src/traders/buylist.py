"""User buy-list — names to own at your price, with a target buy-below price.

Backlog item B5 and the review's highest-leverage behavioural change: instead of
reacting to a fresh daily candidate list (which nudges overtrading), you name the
businesses you'd own and the price you'd pay, and the system tells you when the
market comes to *you*. User data (one row per ticker, migration 009) — the agents
never write it.

``evaluate`` joins each target against the latest close and, when a slice-36
valuation map is supplied, the model's own suggested buy-below price — so a
beginner can sanity-check a self-chosen target against the intrinsic-value
estimate. Pure reads plus three small writers; no agent imports beyond the price
store, so it stays cheap to test.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from traders.prices import PriceHistory, load_history_from_db
from traders.valuation import IntrinsicValue

_SELECT = "SELECT ticker, target_price, note, created_at, updated_at FROM buy_list"


@dataclass(frozen=True)
class BuyTarget:
    """One buy-list entry: a ticker and the price at/under which you'd buy."""

    ticker: str
    target_price: float
    note: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class BuyListRow:
    """A target evaluated against the latest price (and, if known, the model's view)."""

    target: BuyTarget
    latest_price: float | None
    latest_day: str | None
    triggered: bool  # latest price is at or below the target
    distance_pct: float | None  # how far the price sits above the target (negative = below)
    suggested_buy_below: float | None  # slice-36 intrinsic-value buy-below, if available


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_target(
    conn: sqlite3.Connection,
    ticker: str,
    target_price: float,
    note: str | None = None,
) -> BuyTarget:
    """Add or update a ticker's target buy-below price (created_at preserved).

    Raises ``ValueError`` on a non-positive target — a buy-below price must be a
    real positive number to mean anything.
    """
    if target_price is None or target_price <= 0:
        raise ValueError("target_price must be positive")
    now = _now()
    existing = conn.execute(
        "SELECT created_at FROM buy_list WHERE ticker = ?", (ticker,)
    ).fetchone()
    created_at = existing[0] if existing else now
    price = float(target_price)
    conn.execute(
        "INSERT OR REPLACE INTO buy_list (ticker, target_price, note, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (ticker, price, note, created_at, now),
    )
    conn.commit()
    return BuyTarget(ticker, price, note, created_at, now)


def remove_target(conn: sqlite3.Connection, ticker: str) -> bool:
    """Drop a ticker from the buy-list; return whether it was present."""
    cur = conn.execute("DELETE FROM buy_list WHERE ticker = ?", (ticker,))
    conn.commit()
    return cur.rowcount > 0


def load_targets(conn: sqlite3.Connection) -> list[BuyTarget]:
    """All buy-list targets, ordered by ticker."""
    return [BuyTarget(*r) for r in conn.execute(f"{_SELECT} ORDER BY ticker").fetchall()]


def get_target(conn: sqlite3.Connection, ticker: str) -> BuyTarget | None:
    """A single ticker's target, or None when it isn't on the buy-list."""
    row = conn.execute(f"{_SELECT} WHERE ticker = ?", (ticker,)).fetchone()
    return BuyTarget(*row) if row is not None else None


def _latest_close(history: PriceHistory, ticker: str) -> tuple[str, float] | None:
    """The most recent ``(day, close)`` for ``ticker``, or None if it has no prices."""
    series = history.series.get(ticker)
    if not series:
        return None
    return max(series, key=lambda day_close: day_close[0])


def _suggested(valuation: dict[str, IntrinsicValue] | None, ticker: str) -> float | None:
    if not valuation:
        return None
    iv = valuation.get(ticker)
    return iv.buy_below_price if iv is not None else None


def evaluate(
    conn: sqlite3.Connection,
    *,
    history: PriceHistory | None = None,
    valuation: dict[str, IntrinsicValue] | None = None,
) -> list[BuyListRow]:
    """Evaluate every target against the latest close (and an optional valuation map).

    ``triggered`` is True when the latest price is at or below the target;
    ``distance_pct`` is how far the price sits above it (negative once triggered).
    A name with no prices comes back with ``latest_price=None`` and not triggered.
    """
    hist = history if history is not None else load_history_from_db(conn)
    rows: list[BuyListRow] = []
    for target in load_targets(conn):
        suggested = _suggested(valuation, target.ticker)
        latest = _latest_close(hist, target.ticker)
        if latest is None:
            rows.append(BuyListRow(target, None, None, False, None, suggested))
            continue
        day, price = latest
        distance_pct = (price - target.target_price) / target.target_price * 100.0
        rows.append(
            BuyListRow(target, price, day, price <= target.target_price, distance_pct, suggested)
        )
    return rows
