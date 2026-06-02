import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from traders.analyst import run as analyst_run
from traders.db import apply_migrations
from traders.feedback import record_fill, record_sell
from traders.portfolio import run as pm_run
from traders.research import run as research_run
from traders.reviewer import run as reviewer_run
from traders.scout import run as scout_run
from traders.web import queries

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_migrations(conn, MIGRATIONS)
    yield conn
    conn.close()


def _seed(db, tmp_path, tickers, run_date):
    wl = tmp_path / f"wl_{run_date.isoformat()}.json"
    wl.write_text(json.dumps({"sp100": tickers, "eurostoxx50": []}))
    scout_id, _ = scout_run(db, watchlist_path=wl, run_date=run_date, batch_size=len(tickers))
    research_id, _ = research_run(db, scout_run_id=scout_id)
    analyst_id, _ = analyst_run(db, research_run_id=research_id)
    pm_run(db, analyst_run_id=analyst_id)
    return analyst_id


def test_list_theses_no_filters_newest_first(db, tmp_path):
    _seed(db, tmp_path, ["AAA", "BBB"], date(2026, 5, 20))
    rows = queries.list_theses(db)
    assert len(rows) == 2
    assert rows[0].id > rows[1].id  # newest (highest id) first


def test_list_theses_ticker_filter(db, tmp_path):
    _seed(db, tmp_path, ["AAA", "BBB"], date(2026, 5, 20))
    rows = queries.list_theses(db, ticker="aaa")  # case-insensitive
    assert {t.ticker for t in rows} == {"AAA"}


def test_list_theses_min_conviction_filter(db, tmp_path):
    _seed(db, tmp_path, ["AAA"], date(2026, 5, 20))
    assert queries.list_theses(db, min_conviction=1)  # all pass
    assert queries.list_theses(db, min_conviction=6) == []  # none reach 6


def test_list_theses_date_filters(db, tmp_path):
    # created_at is the wall-clock run time, not the seed run_date, so the
    # date filters are exercised relative to the actual stored timestamp.
    _seed(db, tmp_path, ["AAA"], date(2026, 5, 20))
    created_day = queries.list_theses(db)[0].created_at[:10]
    assert queries.list_theses(db, date_from="2099-01-01") == []  # after everything
    assert queries.list_theses(db, date_to="2000-01-01") == []  # before everything
    assert queries.list_theses(db, date_from=created_day)  # inclusive lower bound
    assert queries.list_theses(db, date_to=created_day)  # inclusive upper bound


def test_notes_for_thesis(db, tmp_path):
    _seed(db, tmp_path, ["AAA"], date(2026, 5, 20))
    thesis = queries.list_theses(db, ticker="AAA")[0]
    notes = queries.notes_for_thesis(db, thesis)
    assert notes
    assert "AAA" in notes[0].content
    assert isinstance(notes[0].sources, list)


def test_backlinks_for_thesis(db, tmp_path):
    _seed(db, tmp_path, ["AAA"], date(2026, 5, 20))
    thesis = queries.list_theses(db, ticker="AAA")[0]
    links = queries.backlinks_for_thesis(db, thesis.id)
    assert links
    assert links[0].decision in ("accepted", "rejected")


def test_list_post_mortems(db, tmp_path):
    _seed(db, tmp_path, ["AAA"], date(2026, 5, 20))
    thesis_id = db.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    event = record_fill(db, thesis_id=thesis_id, price=100.0)
    record_sell(db, price=120.0, position_id=event.position_id)
    reviewer_run(db)
    pms = queries.list_post_mortems(db)
    assert len(pms) == 1
    assert pms[0].ticker == "AAA"
    assert pms[0].exit_price == 120.0


# --- routes -----------------------------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from traders.db import connect  # noqa: E402
from traders.web.app import create_app  # noqa: E402


def _seed_path(db_path, tmp_path):
    conn = connect(db_path)
    apply_migrations(conn, MIGRATIONS)
    _seed(conn, tmp_path, ["AAA"], date(2026, 5, 20))
    thesis_id = conn.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    event = record_fill(conn, thesis_id=thesis_id, price=100.0)
    record_sell(conn, price=120.0, position_id=event.position_id)
    reviewer_run(conn)
    conn.close()
    return thesis_id


def test_theses_route_ok(tmp_path):
    db_path = tmp_path / "t.db"
    _seed_path(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    resp = client.get("/theses")
    assert resp.status_code == 200
    assert "AAA" in resp.text
    assert client.get("/theses", params={"ticker": "AAA"}).status_code == 200


def test_theses_list_shows_plain_english_labels(tmp_path):
    db_path = tmp_path / "t.db"
    _seed_path(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    text = client.get("/theses").text
    # AAA hashes to a mean-reversion long in the stub generator (conviction 3),
    # so the row carries the plain headline and the conviction word.
    assert "Buy AAA expecting a bounce back up" in text
    assert "medium" in text  # conviction 3 -> "medium"


def test_thesis_detail_route_ok_and_404(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id = _seed_path(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    assert client.get(f"/theses/{thesis_id}").status_code == 200
    assert client.get("/theses/999999").status_code == 404


def test_thesis_detail_renders_plain_english_layer(tmp_path):
    db_path = tmp_path / "t.db"
    thesis_id = _seed_path(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    text = client.get(f"/theses/{thesis_id}").text
    # plain-English explainer + glossary + friendly research-note labels
    assert "What this means" in text
    assert "Mean-reversion" in text  # glossary term for the stub thesis
    assert "Company financials" in text  # friendly label for the fundamentals note
    # the machine source tag is stripped from the displayed rationale line
    assert "Baseline mean-reversion thesis" in text
    assert "[stub] Baseline" not in text


def test_today_route_explains_accepted_picks(tmp_path):
    db_path = tmp_path / "t.db"
    _seed_path(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    text = client.get("/").text
    # an accepted pick carries the plain-English explainer and links into detail
    assert "Full breakdown" in text
    assert 'href="/theses/' in text


def test_thesis_detail_hides_fill_form_when_position_open(tmp_path):
    # _seed_path closes its position; seed a fresh thesis and leave it OPEN so
    # the has_open_position=True branch of the detail route is exercised.
    db_path = tmp_path / "t.db"
    conn = connect(db_path)
    apply_migrations(conn, MIGRATIONS)
    _seed(conn, tmp_path, ["ZZZ"], date(2026, 5, 21))
    thesis_id = conn.execute("SELECT id FROM theses WHERE ticker = 'ZZZ' LIMIT 1").fetchone()[0]
    record_fill(conn, thesis_id=thesis_id, price=100.0)  # left open, no sell
    conn.close()

    client = TestClient(create_app(db_path))
    resp = client.get(f"/theses/{thesis_id}")
    assert resp.status_code == 200
    assert "has an open position" in resp.text
    # the fill form action must not be rendered while a position is open
    assert f'action="/theses/{thesis_id}/fill"' not in resp.text


def test_reviews_route_ok(tmp_path):
    db_path = tmp_path / "t.db"
    _seed_path(db_path, tmp_path)
    client = TestClient(create_app(db_path))
    resp = client.get("/reviews")
    assert resp.status_code == 200
    assert "AAA" in resp.text


def test_reviews_route_explains_the_trade(tmp_path):
    db_path = tmp_path / "t.db"
    _seed_path(db_path, tmp_path)  # AAA bought at 100, sold at 120 -> +20% gain
    client = TestClient(create_app(db_path))
    text = client.get("/reviews").text
    assert "What happened" in text
    assert "Bought at 100.00, sold at 120.00" in text
    assert "+20.0% gain" in text
