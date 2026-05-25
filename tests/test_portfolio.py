import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from traders.analyst import run as analyst_run
from traders.db import apply_migrations
from traders.portfolio import (
    OpenPosition,
    ThesisRow,
    evaluate,
    run as pm_run,
)
from traders.research import run as research_run
from traders.scout import run as scout_run

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    apply_migrations(conn, MIGRATIONS)
    yield conn
    conn.close()


def _seed_theses(db, tmp_path, tickers, run_date):
    wl = tmp_path / f"wl_{run_date.isoformat()}.json"
    wl.write_text(json.dumps({"sp100": tickers, "eurostoxx50": []}))
    scout_id, _ = scout_run(
        db, watchlist_path=wl, run_date=run_date, batch_size=len(tickers)
    )
    research_id, _ = research_run(db, scout_run_id=scout_id)
    analyst_id, _ = analyst_run(db, research_run_id=research_id)
    return analyst_id


def _t(thesis_id, ticker, conviction=3, size=2.0, t_type="value", direction="long"):
    return ThesisRow(
        thesis_id=thesis_id,
        ticker=ticker,
        thesis_type=t_type,
        direction=direction,
        conviction=conviction,
        suggested_size_pct=size,
    )


def test_evaluate_accepts_all_when_no_conflicts():
    theses = [_t(1, "AAA"), _t(2, "BBB"), _t(3, "CCC")]
    accepted, rejected = evaluate(theses, [], max_total_size_pct=20.0)
    assert {i.ticker for i in accepted} == {"AAA", "BBB", "CCC"}
    assert rejected == []


def test_evaluate_dedupes_keeps_highest_conviction():
    theses = [
        _t(1, "AAA", conviction=3),
        _t(2, "AAA", conviction=5),
        _t(3, "BBB", conviction=2),
    ]
    accepted, rejected = evaluate(theses, [], max_total_size_pct=20.0)
    by_id = {i.thesis_id: i for i in accepted + rejected}
    assert by_id[2].decision == "accepted"
    assert by_id[1].decision == "rejected"
    assert "duplicate" in by_id[1].reason
    assert by_id[3].decision == "accepted"


def test_evaluate_dedup_tiebreak_keeps_earlier_thesis_id():
    theses = [_t(1, "AAA", conviction=3), _t(2, "AAA", conviction=3)]
    accepted, rejected = evaluate(theses, [], max_total_size_pct=20.0)
    assert [i.thesis_id for i in accepted] == [1]
    assert [i.thesis_id for i in rejected] == [2]


def test_evaluate_rejects_thesis_on_held_ticker():
    theses = [_t(1, "AAA", size=2.0)]
    positions = [OpenPosition(ticker="AAA", size_pct=3.0)]
    accepted, rejected = evaluate(theses, positions, max_total_size_pct=20.0)
    assert accepted == []
    assert len(rejected) == 1
    assert "concentration" in rejected[0].reason
    assert "AAA" in rejected[0].reason


def test_evaluate_rejects_when_total_exposure_exceeds_cap():
    theses = [
        _t(1, "AAA", conviction=5, size=5.0),
        _t(2, "BBB", conviction=4, size=5.0),
        _t(3, "CCC", conviction=3, size=5.0),
    ]
    accepted, rejected = evaluate(theses, [], max_total_size_pct=10.0)
    assert {i.thesis_id for i in accepted} == {1, 2}
    assert [i.thesis_id for i in rejected] == [3]
    assert "exposure" in rejected[0].reason


def test_evaluate_counts_open_position_size_in_cap():
    theses = [_t(1, "AAA", size=5.0)]
    positions = [OpenPosition(ticker="BBB", size_pct=8.0)]
    accepted, rejected = evaluate(theses, positions, max_total_size_pct=10.0)
    assert accepted == []
    assert len(rejected) == 1


def test_run_writes_decisions_to_pm_decisions(db, tmp_path):
    analyst_id = _seed_theses(db, tmp_path, ["AAA", "BBB"], date(2026, 5, 20))
    report = pm_run(db, analyst_run_id=analyst_id, max_total_size_pct=20.0)
    assert report.pm_run_id == 1
    rows = db.execute(
        "SELECT pm_run_id, thesis_id, decision FROM pm_decisions ORDER BY id"
    ).fetchall()
    assert len(rows) == len(report.accepted) + len(report.rejected)
    for pm_run_id, _, _ in rows:
        assert pm_run_id == 1


def test_run_defaults_to_latest_analyst_run(db, tmp_path):
    _seed_theses(db, tmp_path, ["AAA"], date(2026, 5, 20))
    latest = _seed_theses(db, tmp_path, ["BBB"], date(2026, 5, 21))
    report = pm_run(db)
    assert report.analyst_run_id == latest
    tickers = {i.ticker for i in report.accepted + report.rejected}
    assert tickers == {"BBB"}


def test_run_with_no_analyst_runs_returns_empty_report(db):
    report = pm_run(db)
    assert report.pm_run_id == 0
    assert report.accepted == []
    assert report.rejected == []
    n = db.execute("SELECT COUNT(*) FROM pm_decisions").fetchone()[0]
    assert n == 0


def test_run_pm_run_id_increments(db, tmp_path):
    analyst_id = _seed_theses(db, tmp_path, ["AAA"], date(2026, 5, 20))
    r1 = pm_run(db, analyst_run_id=analyst_id)
    r2 = pm_run(db, analyst_run_id=analyst_id)
    assert r2.pm_run_id == r1.pm_run_id + 1


def test_run_uses_default_max_total_size_pct(db, tmp_path):
    analyst_id = _seed_theses(db, tmp_path, ["AAA"], date(2026, 5, 20))
    report = pm_run(db, analyst_run_id=analyst_id)
    assert len(report.accepted) == 1
    assert report.accepted[0].ticker == "AAA"


def test_run_respects_open_positions_in_db(db, tmp_path):
    analyst_id = _seed_theses(db, tmp_path, ["AAA"], date(2026, 5, 20))
    db.execute(
        "INSERT INTO positions (ticker, thesis_id, opened_at, size_pct, status)"
        " VALUES ('AAA', 0, '2026-05-19', 3.0, 'open')"
    )
    db.commit()
    report = pm_run(db, analyst_run_id=analyst_id)
    assert report.accepted == []
    assert len(report.rejected) == 1
    assert "AAA" in report.rejected[0].reason
