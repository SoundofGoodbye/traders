"""FastAPI application factory for the local web UI.

`create_app` wires routes over a SQLite database path. Each request gets
its own short-lived connection (SQLite + threadpool safety); migrations
are applied once at startup. Read-only in this slice — the only mutation
surface arrives in slice 13 and routes through `traders.feedback`.

This module imports FastAPI at module scope, so importing it requires the
`web` extra. That's intentional: the module is only imported on the web
path (the CLI imports it lazily; tests `importorskip("fastapi")`). FastAPI
resolves string annotations against module globals, so `Request`/`Depends`
must live here rather than inside the factory.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from traders.db import apply_migrations, connect
from traders.post_mortems import compute_pnl_pct
from traders.web import queries
from traders.web.prices import PriceFn

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _open_position_view(position: queries.Position, price_fn: PriceFn | None) -> dict[str, Any]:
    current = price_fn(position.ticker) if price_fn is not None else None
    pnl = compute_pnl_pct(position.direction or "long", position.entry_price, current)
    return {"p": position, "current_price": current, "pnl_pct": pnl}


def _closed_position_view(position: queries.Position) -> dict[str, Any]:
    pnl = compute_pnl_pct(position.direction or "long", position.entry_price, position.exit_price)
    return {"p": position, "pnl_pct": pnl}


def create_app(db_path: str | Path | None = None, *, price_fn: PriceFn | None = None) -> FastAPI:
    """Build the FastAPI app over `db_path`."""
    # Apply migrations once against a throwaway connection; per-request
    # connections below are opened in their own (threadpool) threads.
    startup = connect(db_path)
    apply_migrations(startup)
    startup.close()

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app = FastAPI(title="traders", docs_url=None, redoc_url=None)

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/", response_class=HTMLResponse)
    def today(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
        pm_run_id = queries.latest_pm_run_id(conn)
        picks = queries.picks_for_run(conn, pm_run_id) if pm_run_id is not None else []
        scout_run_id = queries.latest_scout_run_id(conn)
        candidates = (
            queries.candidates_for_run(conn, scout_run_id)
            if scout_run_id is not None
            else []
        )
        return templates.TemplateResponse(
            request=request,
            name="today.html",
            context={
                "pm_run_id": pm_run_id,
                "accepted": [p for p in picks if p.decision == "accepted"],
                "rejected": [p for p in picks if p.decision == "rejected"],
                "candidates": candidates,
            },
        )

    @app.get("/positions", response_class=HTMLResponse)
    def positions(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
        open_rows = queries.open_positions(conn)
        closed_rows = queries.recently_closed_positions(conn)
        return templates.TemplateResponse(
            request=request,
            name="positions.html",
            context={
                "open_positions": [_open_position_view(p, price_fn) for p in open_rows],
                "closed_positions": [_closed_position_view(p) for p in closed_rows],
                "has_prices": price_fn is not None,
            },
        )

    @app.get("/theses", response_class=HTMLResponse)
    def theses(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
        ticker: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_conviction: str | None = None,
    ) -> Any:
        # Parse min_conviction leniently: a blank form field arrives as ""
        # which we treat as "no filter" rather than a 422.
        min_conv: int | None = None
        if min_conviction not in (None, ""):
            try:
                min_conv = int(min_conviction)
            except ValueError:
                min_conv = None
        rows = queries.list_theses(
            conn,
            ticker=ticker or None,
            date_from=date_from or None,
            date_to=date_to or None,
            min_conviction=min_conv,
        )
        return templates.TemplateResponse(
            request=request,
            name="theses.html",
            context={
                "theses": rows,
                "filters": {
                    "ticker": ticker or "",
                    "date_from": date_from or "",
                    "date_to": date_to or "",
                    "min_conviction": min_conviction or "",
                },
            },
        )

    @app.get("/theses/{thesis_id}", response_class=HTMLResponse)
    def thesis_detail(
        thesis_id: int,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        thesis = queries.thesis_by_id(conn, thesis_id)
        if thesis is None:
            raise HTTPException(status_code=404, detail=f"no thesis {thesis_id}")
        return templates.TemplateResponse(
            request=request,
            name="thesis_detail.html",
            context={
                "thesis": thesis,
                "notes": queries.notes_for_thesis(conn, thesis),
                "backlinks": queries.backlinks_for_thesis(conn, thesis_id),
            },
        )

    @app.get("/reviews", response_class=HTMLResponse)
    def reviews(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
        return templates.TemplateResponse(
            request=request,
            name="reviews.html",
            context={"post_mortems": queries.list_post_mortems(conn)},
        )

    return app
