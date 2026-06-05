"""FastAPI application factory for the local web UI.

`create_app` wires routes over a SQLite database path. Each request gets
its own short-lived connection (SQLite + threadpool safety); migrations
are applied once at startup. Read routes are GET; write actions (slice 13)
are POST and route through `traders.feedback` behind CSRF validation.

This module imports FastAPI at module scope, so importing it requires the
`web` extra. That's intentional: the module is only imported on the web
path (the CLI imports it lazily; tests `importorskip("fastapi")`). FastAPI
resolves string annotations against module globals, so `Request`/`Depends`
must live here rather than inside the factory.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from traders import buylist, feedback, jobs
from traders.db import apply_migrations, connect
from traders.post_mortems import compute_pnl_pct
from traders.prices import load_history_from_db
from traders.valuation import valuations_asof
from traders.web import csrf, explain, queries
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
    # Plain-English display helpers (pure functions from `explain`), exposed to
    # templates the same way `strip_tag` is — so the list/jobs pages can label
    # raw columns without the routes building parallel view dicts.
    templates.env.globals["strip_tag"] = explain.strip_tag
    templates.env.globals["thesis_headline"] = explain.thesis_headline
    templates.env.globals["describe_conviction"] = explain.describe_conviction
    templates.env.globals["describe_position"] = explain.describe_position
    templates.env.globals["describe_schedule"] = explain.describe_schedule
    templates.env.globals["describe_job_status"] = explain.describe_job_status
    app = FastAPI(title="traders", docs_url=None, redoc_url=None)
    # Pin TRADERS_WEB_SECRET to keep CSRF cookies valid across restarts;
    # otherwise a fresh per-process secret is fine for a single-user tool.
    secret = os.environ.get("TRADERS_WEB_SECRET") or csrf.new_secret()
    # jobs.json / cron.log live beside the db, so the UI and the cron wrappers
    # agree (both default to ./data).
    jobs_data_dir = Path(db_path).parent if db_path is not None else None

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def render_with_csrf(request: Request, name: str, context: dict[str, Any]) -> Any:
        """Render a template, ensuring a signed CSRF cookie is present and the
        matching token is available to forms via `csrf_token`."""
        token = csrf.token_from_cookie(secret, request.cookies.get(csrf.COOKIE_NAME))
        cookie_value: str | None = None
        if token is None:
            token, cookie_value = csrf.issue(secret)
        response = templates.TemplateResponse(
            request=request, name=name, context={**context, "csrf_token": token}
        )
        if cookie_value is not None:
            response.set_cookie(csrf.COOKIE_NAME, cookie_value, httponly=True, samesite="strict")
        return response

    async def check_csrf(request: Request) -> None:
        form = await request.form()
        token = form.get(csrf.FIELD_NAME)
        cookie = request.cookies.get(csrf.COOKIE_NAME)
        if not csrf.validate(secret, cookie, token if isinstance(token, str) else None):
            raise HTTPException(status_code=403, detail="CSRF validation failed")

    def form_float(value: Any, field: str, *, required: bool = True) -> float | None:
        if value in (None, ""):
            if required:
                raise HTTPException(status_code=400, detail=f"missing {field}")
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"invalid {field}") from e

    def apply_feedback(fn: Callable[[sqlite3.Connection], Any]) -> None:
        """Open a connection in this coroutine's own thread (SQLite
        check_same_thread) and run a feedback write, mapping the domain
        error to a 400."""
        conn = connect(db_path)
        try:
            fn(conn)
        except feedback.FeedbackError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        finally:
            conn.close()

    def redirect(url: str) -> Any:
        return RedirectResponse(url=url, status_code=303)

    # --- read routes --------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def today(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
        pm_run_id = queries.latest_pm_run_id(conn)
        picks = queries.picks_for_run(conn, pm_run_id) if pm_run_id is not None else []
        scout_run_id = queries.latest_scout_run_id(conn)
        candidates = (
            queries.candidates_for_run(conn, scout_run_id) if scout_run_id is not None else []
        )
        return templates.TemplateResponse(
            request=request,
            name="today.html",
            context={
                "pm_run_id": pm_run_id,
                "accepted": [p for p in picks if p.decision == "accepted"],
                "rejected": [p for p in picks if p.decision == "rejected"],
                "candidates": candidates,
                "explanations": {p.thesis.id: explain.explain_thesis(p.thesis) for p in picks},
            },
        )

    @app.get("/positions", response_class=HTMLResponse)
    def positions(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
        open_rows = queries.open_positions(conn)
        closed_rows = queries.recently_closed_positions(conn)
        return render_with_csrf(
            request,
            "positions.html",
            {
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
        notes = queries.notes_for_thesis(conn, thesis)
        return render_with_csrf(
            request,
            "thesis_detail.html",
            {
                "thesis": thesis,
                "explanation": explain.explain_thesis(thesis),
                "note_views": [
                    {"note": n, "sections": explain.parse_research_note(n.content)} for n in notes
                ],
                "backlinks": queries.backlinks_for_thesis(conn, thesis_id),
                "has_open_position": queries.open_position_for_thesis(conn, thesis_id) is not None,
            },
        )

    @app.get("/reviews", response_class=HTMLResponse)
    def reviews(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
        post_mortems = queries.list_post_mortems(conn)
        return templates.TemplateResponse(
            request=request,
            name="reviews.html",
            context={
                "reviews": [
                    {"pm": pm, "ex": explain.explain_post_mortem(pm)} for pm in post_mortems
                ],
            },
        )

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request) -> Any:
        return render_with_csrf(
            request,
            "jobs.html",
            {
                "jobs": jobs.job_status(jobs_data_dir),
                "log_tail": jobs.read_log_tail(jobs_data_dir),
            },
        )

    @app.get("/buy-list", response_class=HTMLResponse)
    def buy_list_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
        history = load_history_from_db(conn)
        valuation = valuations_asof(conn, history, as_of=date.today())
        rows = buylist.evaluate(conn, history=history, valuation=valuation)
        return render_with_csrf(request, "buy_list.html", {"rows": rows})

    # --- write actions (slice 13) -------------------------------------------
    # POST handlers call the existing traders.feedback functions directly, so
    # there is no parallel write path into `positions`. They are async and open
    # their connection inline (apply_feedback) to keep SQLite use on one thread.
    # After a successful write they 303-redirect back to the originating page.

    @app.post("/theses/{thesis_id}/fill")
    async def post_fill(thesis_id: int, request: Request) -> Any:
        await check_csrf(request)
        form = await request.form()
        price = form_float(form.get("price"), "price")
        size_pct = form_float(form.get("size_pct"), "size_pct", required=False)
        notes = form.get("notes") or None
        apply_feedback(
            lambda c: feedback.record_fill(
                c, thesis_id=thesis_id, price=price, size_pct=size_pct, notes=notes
            )
        )
        return redirect(f"/theses/{thesis_id}")

    @app.post("/theses/{thesis_id}/partial")
    async def post_partial(thesis_id: int, request: Request) -> Any:
        await check_csrf(request)
        form = await request.form()
        price = form_float(form.get("price"), "price")
        size_pct = form_float(form.get("size_pct"), "size_pct")
        notes = form.get("notes") or None
        apply_feedback(
            lambda c: feedback.record_partial(
                c, thesis_id=thesis_id, price=price, size_pct=size_pct, notes=notes
            )
        )
        return redirect(f"/theses/{thesis_id}")

    @app.post("/theses/{thesis_id}/skip")
    async def post_skip(thesis_id: int, request: Request) -> Any:
        await check_csrf(request)
        form = await request.form()
        notes = form.get("notes") or None
        apply_feedback(lambda c: feedback.record_skip(c, thesis_id=thesis_id, notes=notes))
        return redirect(f"/theses/{thesis_id}")

    @app.post("/positions/{position_id}/sell")
    async def post_sell(position_id: int, request: Request) -> Any:
        await check_csrf(request)
        form = await request.form()
        price = form_float(form.get("price"), "price")
        notes = form.get("notes") or None
        apply_feedback(
            lambda c: feedback.record_sell(c, price=price, position_id=position_id, notes=notes)
        )
        return redirect("/positions")

    @app.post("/jobs/{name}/toggle")
    async def post_toggle_job(name: str, request: Request) -> Any:
        await check_csrf(request)
        if name not in jobs.JOBS:
            raise HTTPException(status_code=404, detail=f"no job {name}")
        jobs.set_enabled(name, not jobs.is_enabled(name, jobs_data_dir), jobs_data_dir)
        return redirect("/jobs")

    @app.post("/buy-list/set")
    async def post_buylist_set(request: Request) -> Any:
        await check_csrf(request)
        form = await request.form()
        ticker = str(form.get("ticker") or "").strip()
        if not ticker:
            raise HTTPException(status_code=400, detail="missing ticker")
        target = form_float(form.get("target_price"), "target_price")
        note = form.get("note") or None
        conn = connect(db_path)
        try:
            buylist.set_target(conn, ticker, target, note=note if isinstance(note, str) else None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        finally:
            conn.close()
        return redirect("/buy-list")

    @app.post("/buy-list/remove")
    async def post_buylist_remove(request: Request) -> Any:
        await check_csrf(request)
        form = await request.form()
        ticker = str(form.get("ticker") or "").strip()
        if not ticker:
            raise HTTPException(status_code=400, detail="missing ticker")
        conn = connect(db_path)
        try:
            buylist.remove_target(conn, ticker)
        finally:
            conn.close()
        return redirect("/buy-list")

    return app
