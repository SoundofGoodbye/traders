import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from traders.analyst import run as analyst_run
from traders.db import apply_migrations
from traders.research import run as research_run
from traders.scout import run as scout_run
from traders.signals import DraftThesis

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class FakeGenerator:
    def __init__(self, drafts_by_ticker):
        self.drafts_by_ticker = drafts_by_ticker
        self.calls = []

    def generate(self, ticker, content):
        self.calls.append((ticker, content))
        return list(self.drafts_by_ticker.get(ticker, []))


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_migrations(conn, MIGRATIONS)
    yield conn
    conn.close()


def _seed_research(db, tmp_path, tickers, run_date):
    wl_path = tmp_path / f"wl_{run_date.isoformat()}.json"
    wl_path.write_text(json.dumps({"sp100": tickers, "eurostoxx50": []}))
    scout_id, _ = scout_run(db, watchlist_path=wl_path, run_date=run_date, batch_size=len(tickers))
    research_id, _ = research_run(db, scout_run_id=scout_id)
    return research_id


def _draft(thesis_type="value", direction="long", conviction=3, size=2.0):
    return DraftThesis(
        thesis_type=thesis_type,
        direction=direction,
        conviction=conviction,
        suggested_size_pct=size,
        exit_condition="stop at -10%",
        rationale="because",
    )


def test_run_writes_one_thesis_per_draft(db, tmp_path):
    research_id = _seed_research(db, tmp_path, ["AAA", "BBB"], date(2026, 5, 20))
    fake = FakeGenerator(
        {
            "AAA": [_draft(thesis_type="value")],
            "BBB": [_draft(thesis_type="momentum"), _draft(thesis_type="catalyst")],
        }
    )
    run_id, n = analyst_run(db, generator=fake, research_run_id=research_id)
    assert run_id == 1
    assert n == 3
    rows = db.execute(
        "SELECT ticker, thesis_type, status, run_id, research_run_id FROM theses ORDER BY id"
    ).fetchall()
    assert len(rows) == 3
    by_ticker = {}
    for ticker, t_type, status, r_id, rr_id in rows:
        by_ticker.setdefault(ticker, []).append(t_type)
        assert status == "open"
        assert r_id == run_id
        assert rr_id == research_id
    assert by_ticker == {"AAA": ["value"], "BBB": ["momentum", "catalyst"]}
    assert sorted(c[0] for c in fake.calls) == ["AAA", "BBB"]


def test_run_persists_all_thesis_fields(db, tmp_path):
    research_id = _seed_research(db, tmp_path, ["AAA"], date(2026, 5, 20))
    fake = FakeGenerator(
        {
            "AAA": [
                DraftThesis(
                    thesis_type="catalyst",
                    direction="short",
                    conviction=5,
                    suggested_size_pct=3.5,
                    exit_condition="cover at +20%",
                    rationale="earnings miss expected",
                )
            ]
        }
    )
    analyst_run(db, generator=fake, research_run_id=research_id)
    row = db.execute(
        "SELECT ticker, thesis_type, direction, conviction, suggested_size_pct,"
        " exit_condition, rationale, status FROM theses"
    ).fetchone()
    assert row == (
        "AAA",
        "catalyst",
        "short",
        5,
        3.5,
        "cover at +20%",
        "earnings miss expected",
        "open",
    )


def test_run_zero_theses_for_a_ticker(db, tmp_path):
    research_id = _seed_research(db, tmp_path, ["AAA", "BBB"], date(2026, 5, 20))
    fake = FakeGenerator({"AAA": [_draft()]})  # BBB yields none
    _, n = analyst_run(db, generator=fake, research_run_id=research_id)
    assert n == 1
    tickers = [r[0] for r in db.execute("SELECT ticker FROM theses").fetchall()]
    assert tickers == ["AAA"]
    # Both tickers were considered even though one produced nothing.
    assert sorted(c[0] for c in fake.calls) == ["AAA", "BBB"]


def test_run_defaults_to_latest_research_run(db, tmp_path):
    _seed_research(db, tmp_path, ["AAA"], date(2026, 5, 20))
    latest = _seed_research(db, tmp_path, ["CCC"], date(2026, 5, 21))
    fake = FakeGenerator({"AAA": [_draft()], "CCC": [_draft()]})
    _, n = analyst_run(db, generator=fake)
    assert n == 1
    rows = db.execute("SELECT ticker, research_run_id FROM theses").fetchall()
    assert rows == [("CCC", latest)]


def test_run_with_no_research_runs_returns_empty(db):
    fake = FakeGenerator({})
    run_id, n = analyst_run(db, generator=fake)
    assert run_id == 0
    assert n == 0


def test_run_uses_stub_generator_by_default(db, tmp_path):
    research_id = _seed_research(db, tmp_path, ["AAA", "BBB"], date(2026, 5, 20))
    run_id, n = analyst_run(db, research_run_id=research_id)
    assert run_id == 1
    assert n == 2
    rows = db.execute(
        "SELECT ticker, thesis_type, direction, conviction, suggested_size_pct,"
        " rationale, status FROM theses ORDER BY ticker"
    ).fetchall()
    assert len(rows) == 2
    for ticker, t_type, direction, conviction, size, rationale, status in rows:
        assert t_type in ("value", "catalyst", "momentum", "mean-reversion")
        assert direction in ("long", "short")
        assert 1 <= conviction <= 5
        assert size > 0
        assert ticker in rationale
        assert status == "open"


def test_run_increments_run_id(db, tmp_path):
    research_id = _seed_research(db, tmp_path, ["AAA"], date(2026, 5, 20))
    r1, _ = analyst_run(db, research_run_id=research_id)
    r2, _ = analyst_run(db, research_run_id=research_id)
    assert r2 == r1 + 1


def test_run_with_research_run_but_no_notes_returns_run_id_zero_theses(db, tmp_path):
    # Empty scout run → empty research run → analyst sees 0 notes for that id.
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": [], "eurostoxx50": []}))
    scout_id, _ = scout_run(db, watchlist_path=wl, run_date=date(2026, 5, 20))
    research_id, _ = research_run(db, scout_run_id=scout_id)
    run_id, n = analyst_run(db, research_run_id=research_id)
    assert run_id == 1
    assert n == 0


def test_run_skips_out_of_contract_drafts(db, tmp_path):
    # Defense in depth (audit H1): a draft outside the protocol's ranges is
    # dropped at the Analyst boundary, never persisted for the PM to trust.
    research_id = _seed_research(db, tmp_path, ["AAA", "BBB"], date(2026, 5, 20))
    oversize = _draft(size=999.0)  # size > 100%
    bad_type = _draft(thesis_type="bogus")  # not a known thesis_type
    fake = FakeGenerator({"AAA": [oversize, _draft(size=2.0)], "BBB": [bad_type]})
    _, n = analyst_run(db, generator=fake, research_run_id=research_id)
    assert n == 1
    rows = db.execute("SELECT ticker, suggested_size_pct FROM theses").fetchall()
    assert rows == [("AAA", 2.0)]
