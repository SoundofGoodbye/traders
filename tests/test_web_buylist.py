"""Web tests for the /buy-list page (slice 38) — read view + CSRF-guarded writes."""

import re
import sqlite3

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from traders import buylist  # noqa: E402
from traders.prices import save_prices  # noqa: E402
from traders.web.app import create_app  # noqa: E402


def _csrf_token(client):
    """Prime the CSRF cookie via a GET on /buy-list and return the raw token."""
    resp = client.get("/buy-list")
    match = re.search(r"csrftoken=([^;]+)", resp.headers.get("set-cookie", ""))
    cookie_value = match.group(1) if match else client.cookies.get("csrftoken")
    if cookie_value:
        client.cookies.set("csrftoken", cookie_value)
    return cookie_value.split(".")[0] if cookie_value else None


def test_buy_list_page_renders_empty(tmp_path):
    client = TestClient(create_app(tmp_path / "t.db"))
    resp = client.get("/buy-list")
    assert resp.status_code == 200
    assert "Buy-list" in resp.text
    assert "wait for your price" in resp.text  # the plain-English framing
    assert "No targets yet" in resp.text


def test_buy_list_in_nav(tmp_path):
    client = TestClient(create_app(tmp_path / "t.db"))
    assert "/buy-list" in client.get("/").text  # linked from the global nav


def test_set_adds_target(tmp_path):
    db = tmp_path / "t.db"
    client = TestClient(create_app(db))
    token = _csrf_token(client)
    resp = client.post(
        "/buy-list/set",
        data={"csrf_token": token, "ticker": "AAA", "target_price": "150", "note": "great biz"},
    )
    assert resp.status_code == 200  # followed 303 -> GET /buy-list
    assert "AAA" in resp.text and "great biz" in resp.text
    conn = sqlite3.connect(db)
    target = buylist.get_target(conn, "AAA")
    conn.close()
    assert target is not None and target.target_price == 150.0


def test_set_csrf_rejected(tmp_path):
    db = tmp_path / "t.db"
    client = TestClient(create_app(db))  # no GET -> no cookie
    resp = client.post("/buy-list/set", data={"ticker": "AAA", "target_price": "150"})
    assert resp.status_code == 403
    conn = sqlite3.connect(db)
    assert buylist.get_target(conn, "AAA") is None  # nothing written
    conn.close()


def test_set_rejects_non_positive_target(tmp_path):
    client = TestClient(create_app(tmp_path / "t.db"))
    token = _csrf_token(client)
    resp = client.post(
        "/buy-list/set", data={"csrf_token": token, "ticker": "AAA", "target_price": "0"}
    )
    assert resp.status_code == 400


def test_remove_target(tmp_path):
    db = tmp_path / "t.db"
    client = TestClient(create_app(db))
    token = _csrf_token(client)
    client.post("/buy-list/set", data={"csrf_token": token, "ticker": "AAA", "target_price": "150"})
    resp = client.post("/buy-list/remove", data={"csrf_token": token, "ticker": "AAA"})
    assert resp.status_code == 200
    assert "No targets yet" in resp.text


def test_triggered_target_shows_ready(tmp_path):
    db = tmp_path / "t.db"
    client = TestClient(create_app(db))
    token = _csrf_token(client)
    client.post("/buy-list/set", data={"csrf_token": token, "ticker": "AAA", "target_price": "100"})
    # Seed a latest close at/under the target.
    conn = sqlite3.connect(db)
    save_prices(conn, "AAA", [("2026-01-02", 95.0)])
    conn.close()
    resp = client.get("/buy-list")
    assert "AAA" in resp.text
    assert "Ready" in resp.text
