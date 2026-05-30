import json
import re
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
    """Prime the CSRF cookie via a GET and return the raw token.

    The cookie is HttpOnly + SameSite=strict; httpx's TestClient does not
    auto-persist it into its jar, so we copy it from the Set-Cookie header
    into the client (mirroring what a real browser does on a same-site POST).
    """
    resp = client.get("/positions")
    match = re.search(r"csrftoken=([^;]+)", resp.headers.get("set-cookie", ""))
    cookie_value = match.group(1) if match else client.cookies.get("csrftoken")
    if cookie_value:
        client.cookies.set("csrftoken", cookie_value)
    return cookie_value.split(".")[0] if cookie_value else None


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


def test_partial_opens_position_at_partial_size(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, suggested = _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    token = _csrf_token(client)
    smaller = round(suggested / 2, 2)
    resp = client.post(
        f"/theses/{thesis_id}/partial",
        data={"csrf_token": token, "price": "100", "size_pct": str(smaller)},
    )
    assert resp.status_code == 200
    assert _positions(db_path) == [("AAA", 100.0, None, smaller, "open")]


def test_fill_missing_price_is_400(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, _ = _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    token = _csrf_token(client)
    resp = client.post(f"/theses/{thesis_id}/fill", data={"csrf_token": token})
    assert resp.status_code == 400
    assert _positions(db_path) == []  # nothing written on a rejected request


def test_fill_invalid_price_is_400(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, _ = _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    token = _csrf_token(client)
    resp = client.post(
        f"/theses/{thesis_id}/fill",
        data={"csrf_token": token, "price": "not-a-number"},
    )
    assert resp.status_code == 400
    assert _positions(db_path) == []


def test_partial_missing_size_is_400(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, _ = _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    token = _csrf_token(client)
    # size_pct is required for a partial — a partial without a size is a fill
    resp = client.post(
        f"/theses/{thesis_id}/partial",
        data={"csrf_token": token, "price": "100"},
    )
    assert resp.status_code == 400
    assert _positions(db_path) == []


def test_fill_twice_maps_feedback_error_to_400(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id, _ = _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    token = _csrf_token(client)
    first = client.post(
        f"/theses/{thesis_id}/fill", data={"csrf_token": token, "price": "100"}
    )
    assert first.status_code == 200
    # second fill on a thesis that already has an open position is a domain
    # error in traders.feedback; the web layer surfaces it as a 400, not a 500
    second = client.post(
        f"/theses/{thesis_id}/fill", data={"csrf_token": token, "price": "105"}
    )
    assert second.status_code == 400
    assert _positions(db_path) == [("AAA", 100.0, None, 2.0, "open")]


def test_sell_unknown_position_maps_feedback_error_to_400(tmp_path):
    db_path = tmp_path / "t.db"
    _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    token = _csrf_token(client)
    resp = client.post(
        "/positions/999999/sell", data={"csrf_token": token, "price": "130"}
    )
    assert resp.status_code == 400
