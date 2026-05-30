import json
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from traders.analyst import run as analyst_run  # noqa: E402
from traders.db import apply_migrations, connect  # noqa: E402
from traders.feedback import record_fill  # noqa: E402
from traders.research import run as research_run  # noqa: E402
from traders.scout import run as scout_run  # noqa: E402
from traders.web.app import create_app  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _seed(db_path, tmp_path, ticker="AAA"):
    conn = connect(db_path)
    apply_migrations(conn, MIGRATIONS)
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": [ticker], "eurostoxx50": []}))
    scout_id, _ = scout_run(conn, watchlist_path=wl, run_date=date(2026, 5, 20), batch_size=1)
    research_id, _ = research_run(conn, scout_run_id=scout_id)
    analyst_run(conn, research_run_id=research_id)
    row = conn.execute(
        "SELECT id, suggested_size_pct FROM theses WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    conn.close()
    return int(row[0]), float(row[1])


def _csrf_token(client):
    """Prime the CSRF cookie via a GET and return the raw token."""
    client.get("/positions")
    cookie = client.cookies.get("csrftoken")
    return cookie.split(".")[0] if cookie else None


def _positions(db_path):
    conn = connect(db_path)
    try:
        return conn.execute(
            "SELECT ticker, entry_price, exit_price, size_pct, status FROM positions"
        ).fetchall()
    finally:
        conn.close()


def test_fill_opens_position_matching_cli(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, _ = _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    token = _csrf_token(client)
    resp = client.post(
        f"/theses/{thesis_id}/fill",
        data={"csrf_token": token, "price": "100", "size_pct": "2"},
    )
    assert resp.status_code == 200  # followed 303 -> GET /theses/{id}
    assert _positions(db_path) == [("AAA", 100.0, None, 2.0, "open")]


def test_skip_records_feedback_only(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, _ = _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    token = _csrf_token(client)
    resp = client.post(f"/theses/{thesis_id}/skip", data={"csrf_token": token})
    assert resp.status_code == 200
    assert _positions(db_path) == []  # skip opens no position
    conn = connect(db_path)
    try:
        action = conn.execute(
            "SELECT action FROM feedback WHERE thesis_id = ?", (thesis_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert action == "skip"


def test_sell_closes_open_position(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, _ = _seed(db_path, tmp_path)
    conn = connect(db_path)
    position_id = record_fill(conn, thesis_id=thesis_id, price=100.0).position_id
    conn.close()
    client = TestClient(create_app(db_path))
    token = _csrf_token(client)
    resp = client.post(
        f"/positions/{position_id}/sell", data={"csrf_token": token, "price": "130"}
    )
    assert resp.status_code == 200
    assert _positions(db_path) == [("AAA", 100.0, 130.0, 2.0, "closed")]


def test_csrf_missing_token_is_rejected(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, _ = _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))  # no GET → no cookie
    resp = client.post(f"/theses/{thesis_id}/skip", data={})
    assert resp.status_code == 403
    assert _positions(db_path) == []


def test_csrf_forged_token_is_rejected(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, _ = _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    _csrf_token(client)  # sets a valid cookie
    resp = client.post(
        f"/theses/{thesis_id}/fill",
        data={"csrf_token": "forged", "price": "100"},
    )
    assert resp.status_code == 403
    assert _positions(db_path) == []
