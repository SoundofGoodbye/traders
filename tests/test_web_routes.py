import json
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from traders.analyst import run as analyst_run  # noqa: E402
from traders.db import apply_migrations, connect  # noqa: E402
from traders.feedback import record_fill  # noqa: E402
from traders.portfolio import run as pm_run  # noqa: E402
from traders.research import run as research_run  # noqa: E402
from traders.scout import run as scout_run  # noqa: E402
from traders.web.app import create_app  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _seed(db_path, tmp_path, tickers=("AAA", "BBB")):
    conn = connect(db_path)
    apply_migrations(conn, MIGRATIONS)
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": list(tickers), "eurostoxx50": []}))
    scout_id, _ = scout_run(conn, watchlist_path=wl, run_date=date(2026, 5, 20), batch_size=len(tickers))
    research_id, _ = research_run(conn, scout_run_id=scout_id)
    analyst_id, _ = analyst_run(conn, research_run_id=research_id)
    pm_run(conn, analyst_run_id=analyst_id)
    thesis_id = conn.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    record_fill(conn, thesis_id=thesis_id, price=100.0)
    conn.close()


def test_today_route_ok(tmp_path):
    db_path = tmp_path / "t.db"
    _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "AAA" in resp.text


def test_positions_route_ok(tmp_path):
    db_path = tmp_path / "t.db"
    _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    resp = client.get("/positions")
    assert resp.status_code == 200
    assert "AAA" in resp.text


def test_routes_ok_on_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    client = TestClient(create_app(db_path))
    assert client.get("/").status_code == 200
    assert client.get("/positions").status_code == 200


def test_positions_unrealized_pnl_with_price_fn(tmp_path):
    db_path = tmp_path / "t.db"
    _seed(db_path, tmp_path)
    # long entry at 100, current 150 -> +50.00%
    client = TestClient(create_app(db_path, price_fn=lambda ticker: 150.0))
    resp = client.get("/positions")
    assert resp.status_code == 200
    assert "+50.00%" in resp.text
