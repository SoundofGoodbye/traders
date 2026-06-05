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
    scout_id, _ = scout_run(
        conn, watchlist_path=wl, run_date=date(2026, 5, 20), batch_size=len(tickers)
    )
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


def test_today_route_reframed_low_pressure(tmp_path):
    # B9: the page frames picks as ideas to research, not a daily to-do list.
    db_path = tmp_path / "t.db"
    _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    text = client.get("/").text
    assert "ideas to research" in text.lower()
    assert "do nothing" in text  # most days, the right move
    assert "Buy-list" in text  # points at the patient alternative
    assert "how to record a buy" not in text  # the old action nudge is gone


def test_positions_route_ok(tmp_path):
    db_path = tmp_path / "t.db"
    _seed(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    resp = client.get("/positions")
    assert resp.status_code == 200
    assert "AAA" in resp.text


def test_positions_route_plain_english(tmp_path):
    db_path = tmp_path / "t.db"
    _seed(db_path, tmp_path)  # record_fill opens a long position on AAA
    client = TestClient(create_app(db_path))
    text = client.get("/positions").text
    assert "How to read this page" in text  # the explainer box
    assert "Bought" in text  # long -> past-tense plain word
    assert "on paper" in text  # the unrealized P&L framing


def test_positions_shows_broken_premise(tmp_path):
    # B6: an open position whose business deteriorated gets a premise warning.
    from traders.fundamental_periods import periods_from_statements, save_periods

    db_path = tmp_path / "t.db"
    _seed(db_path, tmp_path)  # opens a long position on AAA
    conn = connect(db_path)
    save_periods(
        conn,
        periods_from_statements(
            "AAA",
            [
                {
                    "period_end": "2024-12-31",
                    "period_type": "annual",
                    "available_at": "2025-02-15",
                    "net_income": -50.0,  # lossmaking -> premise broken
                    "current_assets": 100.0,
                    "current_liabilities": 200.0,
                }
            ],
        ),
    )
    conn.close()
    client = TestClient(create_app(db_path))
    text = client.get("/positions").text
    assert "Premise check" in text
    assert "lossmaking" in text


def test_positions_closed_shows_holding_period(tmp_path):
    # B7: a closed trade shows how long it was held.
    from traders.feedback import record_sell

    db_path = tmp_path / "t.db"
    _seed(db_path, tmp_path)  # opens a long position on AAA
    conn = connect(db_path)
    pos_id = conn.execute("SELECT id FROM positions WHERE status='open' LIMIT 1").fetchone()[0]
    record_sell(conn, price=120.0, position_id=pos_id)
    conn.close()
    client = TestClient(create_app(db_path))
    assert "held" in client.get("/positions").text


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
    assert "end-of-day" in resp.text  # B8: price caveat shown where prices are
