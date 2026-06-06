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

from traders.db import immediate
from traders.parameters import LearnedParameters, load_parameters
from traders.prices import PriceHistory
from traders.signals_lib import (
    closes_before,
    momentum_12_1,
    winsorize,
    zscore,
    zscore_meanrev,
)

DEFAULT_WATCHLIST = Path(__file__).parent / "data" / "watchlist.json"
DEFAULT_BATCH_SIZE = 10
RANK_MIN_HISTORY = 60


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


def rank_candidates(
    watchlist: list[str],
    history: PriceHistory,
    as_of: date,
    batch_size: int,
    min_history: int = RANK_MIN_HISTORY,
) -> list[str]:
    """Rank the watchlist by a composite price signal; return the top ``batch_size``.

    Each name scores on two cross-sectionally-standardized axes — 12-1 momentum
    and oversold-ness (negated 20-day mean-reversion z) — and takes the stronger
    of the two, so both trend leaders and washed-out names surface. Names without
    enough look-ahead-safe history (signals computed only from closes before
    ``as_of``) are dropped. Returns fewer than ``batch_size`` when few names have
    signals, and ``[]`` when none do (the caller falls back to rotation).
    """
    moms: list[float | None] = []
    oss: list[float | None] = []
    for ticker in watchlist:
        closes = closes_before(history, ticker, as_of)
        if len(closes) < min_history:
            moms.append(None)
            oss.append(None)
            continue
        moms.append(momentum_12_1(closes))
        mr = zscore_meanrev(closes, 20)
        oss.append(None if mr is None else -mr)
    mom_z = zscore(winsorize(moms))
    os_z = zscore(winsorize(oss))
    scored: list[tuple[float, int, str]] = []
    for i, ticker in enumerate(watchlist):
        axes = [z for z in (mom_z[i], os_z[i]) if z is not None]
        if not axes:
            continue
        scored.append((max(axes), i, ticker))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [ticker for _score, _i, ticker in scored[:batch_size]]


def _next_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(scout_run_id), 0) FROM candidates").fetchone()
    return int(row[0]) + 1


def run(
    conn: sqlite3.Connection,
    watchlist_path: Path | None = None,
    run_date: date | None = None,
    batch_size: int | None = None,
    params: LearnedParameters | None = None,
    history: PriceHistory | None = None,
) -> tuple[int, list[str]]:
    """Run the Scout: load watchlist, select candidates, write to `candidates`.

    ``batch_size`` overrides the learned parameter when given (tests use
    it); otherwise it comes from the active `LearnedParameters`.

    When a ``history`` is supplied the candidates are signal-ranked from prices;
    if no name has enough price history to rank, it falls back to the date
    rotation so the pipeline always produces candidates. With no ``history``
    (the default) it uses the rotation, exactly as before.
    """
    bs = batch_size if batch_size is not None else (params or load_parameters()).batch_size
    watchlist = load_watchlist(watchlist_path)
    rd = run_date or datetime.now(timezone.utc).date()
    if history is not None:
        picks = rank_candidates(watchlist, history, rd, bs)
        if picks:
            reason = f"signal-ranked for {rd.isoformat()}"
        else:
            picks = filter_candidates(watchlist, rd, bs)
            reason = f"rotation fallback (no price signals) for {rd.isoformat()}"
    else:
        picks = filter_candidates(watchlist, rd, bs)
        reason = f"rotation window for {rd.isoformat()}"
    created_at = datetime.now(timezone.utc).isoformat()
    with immediate(conn):
        run_id = _next_run_id(conn)
        conn.executemany(
            "INSERT INTO candidates (ticker, scout_run_id, reason, created_at) VALUES (?, ?, ?, ?)",
            [(t, run_id, reason, created_at) for t in picks],
        )
    return run_id, picks
