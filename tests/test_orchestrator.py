import json
import sqlite3

import pytest

from traders.data_sources import DataPoint
from traders.db import apply_migrations, connect
from traders.orchestrator import run_daily, run_weekly


def _watchlist(tmp_path, tickers=("AAA", "BBB")):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": list(tickers), "eurostoxx50": []}))
    return wl


def test_run_daily_chains_all_four_agents(tmp_path):
    db = tmp_path / "t.db"
    wl = _watchlist(tmp_path)
    conn = connect(db)
    apply_migrations(conn)
    result = run_daily(conn, watchlist_path=wl, batch_size=2)
    conn.close()

    assert result.scout_run_id == 1
    assert result.research_run_id == 1
    assert result.analyst_run_id == 1
    assert result.report.pm_run_id == 1
    assert result.report.analyst_run_id == 1
    assert set(result.scout_picks) == {"AAA", "BBB"}
    assert set(result.research_tickers) == {"AAA", "BBB"}
    assert result.analyst_thesis_count == 2

    decisions = len(result.report.accepted) + len(result.report.rejected)
    assert decisions == 2

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM research_notes").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM theses").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM pm_decisions").fetchone()[0] == 2
    conn.close()


def test_run_daily_increments_run_ids_across_calls(tmp_path):
    db = tmp_path / "t.db"
    wl = _watchlist(tmp_path)
    conn = connect(db)
    apply_migrations(conn)
    first = run_daily(conn, watchlist_path=wl, batch_size=2)
    second = run_daily(conn, watchlist_path=wl, batch_size=2)
    conn.close()
    assert first.scout_run_id == 1
    assert second.scout_run_id == 2
    assert second.research_run_id == 2
    assert second.analyst_run_id == 2
    assert second.report.pm_run_id == 2
    assert second.report.analyst_run_id == 2


def test_run_daily_threads_custom_data_source(tmp_path):
    """`run_daily(data_source=...)` reaches the Researcher step."""

    class RecordingDataSource:
        def __init__(self):
            self.calls = []

        def fetch(self, ticker):
            self.calls.append(ticker)
            return [
                DataPoint(
                    kind="news",
                    title=f"{ticker} headline",
                    url=f"https://example.com/{ticker}",
                    snippet="snip",
                    published_at="2026-05-20",
                )
            ]

    db = tmp_path / "t.db"
    wl = _watchlist(tmp_path)
    conn = connect(db)
    apply_migrations(conn)
    recorder = RecordingDataSource()
    result = run_daily(conn, watchlist_path=wl, batch_size=2, data_source=recorder)
    conn.close()

    assert sorted(recorder.calls) == ["AAA", "BBB"]
    assert sorted(result.research_tickers) == ["AAA", "BBB"]

    conn = sqlite3.connect(db)
    sources = conn.execute("SELECT sources FROM research_notes").fetchall()
    conn.close()
    # Sources must reflect the injected data source (https://example.com/...),
    # not the stub's stub:// URLs.
    for (raw,) in sources:
        parsed = json.loads(raw)
        assert any(item["url"].startswith("https://example.com") for item in parsed)


def test_run_daily_propagates_agent_errors(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    apply_migrations(conn)
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(FileNotFoundError):
        run_daily(conn, watchlist_path=missing, batch_size=2)
    conn.close()


def test_run_daily_warns_on_empty_stage(tmp_path, caplog):
    import logging

    wl = _watchlist(tmp_path, tickers=())  # empty watchlist -> zero candidates
    conn = connect(tmp_path / "t.db")
    apply_migrations(conn)
    with caplog.at_level(logging.WARNING, logger="traders.orchestrator"):
        run_daily(conn, watchlist_path=wl, batch_size=2)
    conn.close()
    assert any("no candidates" in r.getMessage().lower() for r in caplog.records)


def test_run_weekly_no_closed_positions(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    apply_migrations(conn)
    result = run_weekly(conn)
    conn.close()
    assert result.reviewer_run_id == 0
    assert result.post_mortems_written == 0


def test_run_weekly_with_closed_position(tmp_path):
    db = tmp_path / "t.db"
    wl = _watchlist(tmp_path, tickers=("AAA",))
    conn = connect(db)
    apply_migrations(conn)
    run_daily(conn, watchlist_path=wl, batch_size=1)
    thesis_id = conn.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, closed_at, entry_price, exit_price,"
        " size_pct, status)"
        " VALUES ('AAA', ?, '2026-05-01', '2026-05-20', 100.0, 120.0, 2.0,"
        " 'closed')",
        (thesis_id,),
    )
    conn.commit()
    result = run_weekly(conn)
    conn.close()
    assert result.reviewer_run_id == 1
    assert result.post_mortems_written == 1

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM post_mortems").fetchone()[0] == 1
    conn.close()
