import json
import sqlite3

import pytest

from traders.cli import main


def test_cli_scout_then_research(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "2"])
    capsys.readouterr()
    main(["research", "--db", str(db)])
    out = capsys.readouterr().out
    assert "research run" in out
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM research_notes").fetchone()[0]
    conn.close()
    assert n == 2


def test_cli_research_no_scout_runs(tmp_path, capsys):
    db = tmp_path / "t.db"
    main(["research", "--db", str(db)])
    out = capsys.readouterr().out
    assert "0 note" in out


def test_cli_scout_research_analyse(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "2"])
    main(["research", "--db", str(db)])
    capsys.readouterr()
    main(["analyse", "--db", str(db)])
    out = capsys.readouterr().out
    assert "analyst run" in out
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM theses").fetchone()[0]
    conn.close()
    assert n == 2


def test_cli_analyse_no_research_runs(tmp_path, capsys):
    db = tmp_path / "t.db"
    main(["analyse", "--db", str(db)])
    out = capsys.readouterr().out
    assert "0 thesis" in out


def test_cli_analyse_signals_generator_no_prices(tmp_path, capsys):
    # --generator signals with no ingested prices yields no theses, hermetically.
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db)])
    capsys.readouterr()
    main(["analyse", "--db", str(db), "--generator", "signals"])
    out = capsys.readouterr().out
    assert "generator: signals" in out
    assert "0 thesis" in out


def test_cli_pm_full_pipeline(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "2"])
    main(["research", "--db", str(db)])
    main(["analyse", "--db", str(db)])
    capsys.readouterr()
    main(["pm", "--db", str(db)])
    out = capsys.readouterr().out
    assert "pm run" in out
    assert "accepted" in out
    assert "rejected" in out
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM pm_decisions").fetchone()[0]
    conn.close()
    assert n == 2


def test_cli_pm_no_analyst_runs(tmp_path, capsys):
    db = tmp_path / "t.db"
    main(["pm", "--db", str(db)])
    out = capsys.readouterr().out
    assert "0 accepted" in out


def test_cli_review_full_pipeline(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db_path), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db_path)])
    main(["analyse", "--db", str(db_path)])
    main(["pm", "--db", str(db_path)])
    conn = sqlite3.connect(db_path)
    thesis_id = conn.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, closed_at, entry_price, exit_price,"
        " size_pct, status)"
        " VALUES ('AAA', ?, '2026-05-01', '2026-05-20', 100.0, 120.0, 2.0, 'closed')",
        (thesis_id,),
    )
    conn.commit()
    conn.close()
    capsys.readouterr()
    main(["review", "--db", str(db_path)])
    out = capsys.readouterr().out
    assert "reviewer run" in out
    assert "1 post-mortem" in out
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM post_mortems").fetchone()[0]
    conn.close()
    assert n == 1


def test_cli_review_no_positions(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    main(["review", "--db", str(db_path)])
    out = capsys.readouterr().out
    assert "0 post-mortem" in out


def test_cli_feedback_fill_then_sell_then_review(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db_path), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db_path)])
    main(["analyse", "--db", str(db_path)])
    main(["pm", "--db", str(db_path)])
    conn = sqlite3.connect(db_path)
    thesis_id = conn.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    conn.close()
    capsys.readouterr()

    main(
        [
            "feedback",
            "fill",
            "--db",
            str(db_path),
            "--thesis-id",
            str(thesis_id),
            "--price",
            "100.0",
        ]
    )
    out = capsys.readouterr().out
    assert "fill recorded" in out
    assert "opened position" in out

    conn = sqlite3.connect(db_path)
    position_id, status = conn.execute(
        "SELECT id, status FROM positions WHERE thesis_id = ?", (thesis_id,)
    ).fetchone()
    conn.close()
    assert status == "open"

    main(
        [
            "feedback",
            "sell",
            "--db",
            str(db_path),
            "--position-id",
            str(position_id),
            "--price",
            "120.0",
        ]
    )
    out = capsys.readouterr().out
    assert "sell recorded" in out

    conn = sqlite3.connect(db_path)
    status = conn.execute("SELECT status FROM positions WHERE id = ?", (position_id,)).fetchone()[0]
    conn.close()
    assert status == "closed"

    main(["review", "--db", str(db_path)])
    out = capsys.readouterr().out
    assert "1 post-mortem" in out


def test_cli_feedback_skip(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db_path), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db_path)])
    main(["analyse", "--db", str(db_path)])
    conn = sqlite3.connect(db_path)
    thesis_id = conn.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    conn.close()
    capsys.readouterr()

    main(
        [
            "feedback",
            "skip",
            "--db",
            str(db_path),
            "--thesis-id",
            str(thesis_id),
            "--notes",
            "no budget",
        ]
    )
    out = capsys.readouterr().out
    assert "skip recorded" in out
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    action = conn.execute("SELECT action FROM feedback").fetchone()[0]
    conn.close()
    assert n == 0
    assert action == "skip"


def test_cli_pm_markdown_to_stdout(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "2"])
    main(["research", "--db", str(db)])
    main(["analyse", "--db", str(db)])
    capsys.readouterr()
    main(["pm", "--db", str(db), "--format", "markdown"])
    out = capsys.readouterr().out
    assert out.startswith("# Daily Report")
    assert "## Accepted" in out
    assert "## Rejected" in out
    assert "AAA" in out
    assert "pm run" not in out


def test_cli_pm_markdown_to_output_file(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db)])
    main(["analyse", "--db", str(db)])
    capsys.readouterr()
    target = tmp_path / "out" / "report.md"
    main(["pm", "--db", str(db), "--format", "markdown", "--output", str(target)])
    assert target.exists()
    content = target.read_text()
    assert content.startswith("# Daily Report")
    out = capsys.readouterr().out
    assert "wrote" in out
    assert str(target) in out


def test_cli_pm_text_default_unchanged(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db)])
    main(["analyse", "--db", str(db)])
    capsys.readouterr()
    main(["pm", "--db", str(db)])
    out = capsys.readouterr().out
    assert "pm run" in out
    assert "# Daily Report" not in out


def test_cli_review_markdown_to_stdout(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db_path), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db_path)])
    main(["analyse", "--db", str(db_path)])
    conn = sqlite3.connect(db_path)
    thesis_id = conn.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, closed_at, entry_price, exit_price,"
        " size_pct, status)"
        " VALUES ('AAA', ?, '2026-05-01', '2026-05-20', 100.0, 120.0, 2.0, 'closed')",
        (thesis_id,),
    )
    conn.commit()
    conn.close()
    capsys.readouterr()
    main(["review", "--db", str(db_path), "--format", "markdown"])
    out = capsys.readouterr().out
    assert out.startswith("# Weekly Review")
    assert "AAA" in out
    assert "PnL:" in out
    assert "post-mortem(s)" not in out


def test_cli_review_markdown_no_post_mortems_renders_placeholder(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    main(["review", "--db", str(db_path), "--format", "markdown"])
    out = capsys.readouterr().out
    assert "# Weekly Review" in out
    assert "No post-mortems" in out


def test_cli_review_markdown_to_output_file(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db_path), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db_path)])
    main(["analyse", "--db", str(db_path)])
    conn = sqlite3.connect(db_path)
    thesis_id = conn.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, closed_at, entry_price, exit_price,"
        " size_pct, status)"
        " VALUES ('AAA', ?, '2026-05-01', '2026-05-20', 100.0, 120.0, 2.0, 'closed')",
        (thesis_id,),
    )
    conn.commit()
    conn.close()
    capsys.readouterr()
    target = tmp_path / "out" / "review.md"
    main(
        [
            "review",
            "--db",
            str(db_path),
            "--format",
            "markdown",
            "--output",
            str(target),
        ]
    )
    assert target.exists()
    content = target.read_text()
    assert content.startswith("# Weekly Review")
    assert "AAA" in content


def test_cli_run_daily_chains_to_pm_report(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    main(
        [
            "run-daily",
            "--db",
            str(db),
            "--watchlist",
            str(wl),
            "--batch-size",
            "2",
        ]
    )
    out = capsys.readouterr().out
    assert "scout run 1" in out
    assert "research run 1" in out
    assert "analyst run 1" in out
    assert "pm run 1" in out
    assert "accepted" in out
    assert "rejected" in out
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM pm_decisions").fetchone()[0] == 2
    conn.close()


def test_cli_run_daily_markdown_pipes_clean(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(
        [
            "run-daily",
            "--db",
            str(db),
            "--watchlist",
            str(wl),
            "--batch-size",
            "1",
            "--format",
            "markdown",
        ]
    )
    out = capsys.readouterr().out
    assert out.startswith("# Daily Report")
    assert "scout run" not in out
    assert "research run" not in out
    assert "analyst run" not in out
    assert "pm run 1" not in out


def test_cli_run_daily_markdown_to_output_keeps_progress(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    target = tmp_path / "out" / "daily.md"
    main(
        [
            "run-daily",
            "--db",
            str(db),
            "--watchlist",
            str(wl),
            "--batch-size",
            "1",
            "--format",
            "markdown",
            "--output",
            str(target),
        ]
    )
    out = capsys.readouterr().out
    assert "scout run 1" in out
    assert "wrote" in out
    assert target.exists()
    content = target.read_text()
    assert content.startswith("# Daily Report")


def test_cli_run_weekly_no_positions(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    main(["run-weekly", "--db", str(db_path)])
    out = capsys.readouterr().out
    assert "reviewer run 0" in out
    assert "0 post-mortem" in out


def test_cli_metrics_empty_db(tmp_path, capsys):
    db = tmp_path / "t.db"
    main(["metrics", "--db", str(db)])
    out = capsys.readouterr().out
    assert "Strategy scorecard" in out
    assert "INSUFFICIENT_DATA" in out


def test_cli_params_shows_defaults(tmp_path, capsys):
    main(["params"])
    out = capsys.readouterr().out
    assert "batch_size: 10" in out
    assert "max_total_size_pct: 20.0" in out


def test_cli_optimize_no_proposal_on_empty_db(tmp_path, capsys):
    db = tmp_path / "t.db"
    main(["optimize", "--db", str(db)])
    out = capsys.readouterr().out
    assert "no proposal" in out


def test_cli_run_weekly_with_closed_position(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(
        [
            "run-daily",
            "--db",
            str(db_path),
            "--watchlist",
            str(wl),
            "--batch-size",
            "1",
        ]
    )
    conn = sqlite3.connect(db_path)
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
    conn.close()
    capsys.readouterr()

    main(["run-weekly", "--db", str(db_path), "--format", "markdown"])
    out = capsys.readouterr().out
    assert out.startswith("# Weekly Review")
    assert "AAA" in out
    assert "PnL:" in out


def test_cli_research_data_source_flag_defaults_to_stub(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "1"])
    capsys.readouterr()
    main(["research", "--db", str(db)])
    out = capsys.readouterr().out
    assert "(source: stub)" in out


def test_cli_research_explicit_stub_data_source(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "1"])
    capsys.readouterr()
    main(["research", "--db", str(db), "--data-source", "stub"])
    out = capsys.readouterr().out
    assert "(source: stub)" in out


def test_cli_research_rejects_unknown_data_source(tmp_path):
    db = tmp_path / "t.db"
    with pytest.raises(SystemExit):
        main(["research", "--db", str(db), "--data-source", "polygon"])


def test_cli_run_daily_logs_data_source(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(
        [
            "run-daily",
            "--db",
            str(db),
            "--watchlist",
            str(wl),
            "--batch-size",
            "1",
            "--data-source",
            "stub",
        ]
    )
    out = capsys.readouterr().out
    assert "(source: stub)" in out


def test_cli_backtest_synthetic_runs(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB", "CCC"], "eurostoxx50": []}))
    main(
        [
            "backtest",
            "--db",
            str(db),
            "--watchlist",
            str(wl),
            "--start",
            "2026-01-01",
            "--end",
            "2026-03-31",
            "--batch-size",
            "2",
        ]
    )
    out = capsys.readouterr().out
    assert "Backtest — verdict:" in out
    assert "trades:" in out


def test_cli_backtest_markdown_to_stdout(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    main(
        [
            "backtest",
            "--db",
            str(db),
            "--watchlist",
            str(wl),
            "--start",
            "2026-01-01",
            "--end",
            "2026-02-28",
            "--format",
            "markdown",
        ]
    )
    out = capsys.readouterr().out
    assert out.startswith("# Backtest")


def test_cli_backtest_signals_strategy_runs(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB", "CCC"], "eurostoxx50": []}))
    main(
        [
            "backtest",
            "--db",
            str(db),
            "--watchlist",
            str(wl),
            "--start",
            "2024-01-01",
            "--end",
            "2026-03-01",
            "--strategy",
            "signals",
        ]
    )
    out = capsys.readouterr().out
    assert "signals strategy" in out


def test_cli_backtest_oos_split(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    main(
        [
            "backtest",
            "--db",
            str(db),
            "--watchlist",
            str(wl),
            "--start",
            "2026-01-01",
            "--end",
            "2026-05-31",
            "--oos-fraction",
            "0.3",
        ]
    )
    out = capsys.readouterr().out
    assert "In-sample" in out
    assert "Out-of-sample" in out


def test_cli_backtest_compare_missing_experiment_exits_nonzero(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "backtest",
                "--db",
                str(db),
                "--watchlist",
                str(wl),
                "--start",
                "2026-01-01",
                "--end",
                "2026-02-28",
                "--compare-experiment",
                "99",
            ]
        )
    assert exc.value.code == 1
    assert "backtest error" in capsys.readouterr().out


def test_cli_scout_rank_signals_falls_back_without_prices(tmp_path, capsys):
    # --rank signals with no ingested prices falls back to rotation, hermetically.
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    main(
        ["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "2", "--rank", "signals"]
    )
    out = capsys.readouterr().out
    assert "scout run 1: 2 candidate(s)" in out


def test_cli_ingest_prices_skips_unmapped(tmp_path, capsys):
    # An unmappable ticker is skipped before any network call — fully hermetic.
    db = tmp_path / "t.db"
    main(["ingest-prices", "--source", "stooq", "--db", str(db), "--ticker", "FOO.ZZ"])
    out = capsys.readouterr().out
    assert "0 close(s)" in out
    assert "skipped: FOO.ZZ" in out


def test_cli_ingest_prices_tiingo_missing_token_exits_cleanly(tmp_path, monkeypatch):
    # Default source is Tiingo, which needs a token — fail fast with a clear message.
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # isolate from any real ./.env the CLI would load
    db = tmp_path / "t.db"
    with pytest.raises(SystemExit) as exc:
        main(["ingest-prices", "--db", str(db), "--ticker", "AAPL"])
    assert "TIINGO_API_KEY" in str(exc.value)


def test_cli_ingest_prices_tiingo_writes(tmp_path, capsys, monkeypatch):
    # Patch the real Tiingo fetcher with a canned one so the CLI path is hermetic.
    import traders.tiingo as tiingo

    csv_text = (
        "date,close,high,low,open,volume,adjClose,adjHigh,adjLow,adjOpen,adjVolume,divCash,splitFactor\n"
        "2026-01-02,190,191,189,190,1000,95.0,95,94,95,1000,0,1\n"
        "2026-01-03,192,193,191,191,1100,96.0,96,95,95,1100,0,1\n"
    )
    monkeypatch.setattr(
        tiingo, "_default_tiingo_fetcher", lambda start_date=None: lambda sym: csv_text
    )
    db = tmp_path / "t.db"
    main(["ingest-prices", "--db", str(db), "--ticker", "AAPL", "--delay", "0"])
    out = capsys.readouterr().out
    assert "ingested 2 close(s)" in out
    assert "source: tiingo" in out
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM prices WHERE ticker = 'AAPL'").fetchone()[0]
    conn.close()
    assert n == 2


def test_cli_feedback_error_exits_nonzero(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "feedback",
                "fill",
                "--db",
                str(db_path),
                "--thesis-id",
                "999",
                "--price",
                "100.0",
            ]
        )
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "feedback error" in out


# ---- optimize --apply OOS gate (slice 24) --------------------------------


def _seed_proposal(db_path) -> int:
    """Insert one pending drawdown proposal (max_total_size_pct 20 -> 16)."""
    from traders.db import apply_migrations, connect
    from traders.optimizer import propose_experiment
    from traders.parameters import LearnedParameters
    from traders.strategy import StrategyGoal

    conn = connect(db_path)
    apply_migrations(conn)
    rows = [
        ("AAA", 100, 110, "2025-01-10T00:00:00+00:00"),
        ("BBB", 100, 90, "2025-01-12T00:00:00+00:00"),
        ("CCC", 100, 120, "2025-01-14T00:00:00+00:00"),
        ("DDD", 100, 95, "2025-01-16T00:00:00+00:00"),
    ]
    for ticker, entry, exit_, closed_at in rows:
        cur = conn.execute(
            "INSERT INTO theses (ticker, thesis_type, direction, conviction,"
            " suggested_size_pct, created_at, status)"
            " VALUES (?, 'momentum', 'long', 3, 5.0,"
            " '2025-01-01T00:00:00+00:00', 'open')",
            (ticker,),
        )
        conn.execute(
            "INSERT INTO positions (thesis_id, ticker, status, size_pct,"
            " entry_price, exit_price, opened_at, closed_at)"
            " VALUES (?, ?, 'closed', 5.0, ?, ?, '2025-01-01T00:00:00+00:00', ?)",
            (cur.lastrowid, ticker, entry, exit_, closed_at),
        )
    conn.commit()
    # A goal where only drawdown fails -> proposes lowering the exposure cap.
    exp = propose_experiment(
        conn,
        goal=StrategyGoal("g", "", 5.0, 5.0, 0.5, 0.1, 4),
        params=LearnedParameters(),
    )
    conn.close()
    assert exp is not None
    return exp.id


def _status(db_path, exp_id: int) -> str:
    from traders.db import connect
    from traders.optimizer import get_experiment

    conn = connect(db_path)
    exp = get_experiment(conn, exp_id)
    conn.close()
    return exp.status


def test_cli_optimize_apply_blocked_by_gate(tmp_path, capsys):
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA", "BBB"], "eurostoxx50": []}))
    exp_id = _seed_proposal(db)
    capsys.readouterr()
    # Synthetic source with two names: the cap change can't earn its keep
    # out-of-sample, so the gate withholds the apply.
    main(
        [
            "optimize",
            "--apply",
            str(exp_id),
            "--db",
            str(db),
            "--source",
            "synthetic",
            "--watchlist",
            str(wl),
            "--start",
            "2026-01-01",
            "--end",
            "2026-04-30",
        ]
    )
    out = capsys.readouterr().out
    assert "Optimizer OOS gate" in out
    assert "not applied" in out
    assert "synthetic price source" in out  # the illustrative-data warning
    assert _status(db, exp_id) == "proposed"  # untouched — gate blocked it


def test_cli_optimize_apply_force_bypasses_gate(tmp_path, capsys):
    db = tmp_path / "t.db"
    exp_id = _seed_proposal(db)
    lp = tmp_path / "lp.json"  # hermetic params target, not the repo's
    capsys.readouterr()
    main(
        [
            "optimize",
            "--apply",
            str(exp_id),
            "--db",
            str(db),
            "--force",
            "--params",
            str(lp),
        ]
    )
    out = capsys.readouterr().out
    assert "gate bypassed via --force" in out
    assert _status(db, exp_id) == "applied"
    from traders.parameters import load_parameters

    assert lp.exists()
    assert load_parameters(lp).max_total_size_pct == 16.0


def test_cli_optimize_apply_unknown_id_exits_nonzero(tmp_path, capsys):
    db = tmp_path / "t.db"
    with pytest.raises(SystemExit) as exc:
        main(["optimize", "--apply", "999", "--db", str(db)])
    assert exc.value.code == 1
    assert "optimize error" in capsys.readouterr().out


# ---- ingest-fundamentals (slice 25) --------------------------------------


def test_cli_ingest_fundamentals_writes(tmp_path, capsys, monkeypatch):
    # Patch the real yfinance fetcher with a canned one so the CLI path is hermetic.
    import traders.fundamentals as fundamentals

    monkeypatch.setattr(
        fundamentals,
        "_default_yf_fetcher",
        lambda: lambda ticker: {"marketCap": 1.0e9, "trailingEps": 2.0},
    )
    db = tmp_path / "t.db"
    main(
        [
            "ingest-fundamentals",
            "--db",
            str(db),
            "--ticker",
            "AAA",
            "--ticker",
            "BBB",
            "--as-of",
            "2026-06-01",
            "--delay",
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert "ingested fundamentals for 2 ticker(s)" in out
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
    conn.close()
    assert n == 2


def test_cli_ingest_fundamentals_without_realdata_exits_cleanly(tmp_path):
    import importlib.util

    if importlib.util.find_spec("yfinance") is not None:
        pytest.skip("yfinance installed — the missing-extra path can't be exercised")
    db = tmp_path / "t.db"
    with pytest.raises(SystemExit) as exc:
        main(["ingest-fundamentals", "--db", str(db), "--ticker", "AAA", "--delay", "0"])
    assert "realdata" in str(exc.value)


# ---- ingest-fundamental-periods (slice 33) -------------------------------


def test_cli_ingest_fundamental_periods_writes(tmp_path, capsys, monkeypatch):
    # Patch the real yfinance statements fetcher with a canned one so the CLI
    # path is hermetic.
    import traders.fundamental_periods as fp

    canned = [
        {
            "period_end": "2024-12-31",
            "period_type": "annual",
            "net_income": 100.0,
            "total_assets": 2000.0,
            "available_at": "2025-02-15",
        },
        {
            "period_end": "2023-12-31",
            "period_type": "annual",
            "net_income": 90.0,
            "total_assets": 1900.0,
            "available_at": "2024-02-15",
        },
    ]
    monkeypatch.setattr(fp, "_default_yf_statements_fetcher", lambda: lambda ticker: canned)
    db = tmp_path / "t.db"
    main(
        [
            "ingest-fundamental-periods",
            "--db",
            str(db),
            "--ticker",
            "AAA",
            "--ticker",
            "BBB",
            "--delay",
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert "ingested 4 period(s) across 2 ticker(s)" in out
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM fundamental_periods").fetchone()[0]
    conn.close()
    assert n == 4


def test_cli_ingest_fundamental_periods_without_realdata_exits_cleanly(tmp_path):
    import importlib.util

    if importlib.util.find_spec("yfinance") is not None:
        pytest.skip("yfinance installed — the missing-extra path can't be exercised")
    db = tmp_path / "t.db"
    with pytest.raises(SystemExit) as exc:
        main(["ingest-fundamental-periods", "--db", str(db), "--ticker", "AAA", "--delay", "0"])
    assert "realdata" in str(exc.value)


# ---- buylist (slice 37) --------------------------------------------------


def test_cli_buylist_set_and_status(tmp_path, capsys):
    from traders.prices import save_prices

    db = tmp_path / "t.db"
    main(["buylist", "--db", str(db), "set", "--ticker", "AAA", "--target", "100"])
    assert "set AAA" in capsys.readouterr().out
    # Seed a price at/under the target so status reports it triggered.
    conn = sqlite3.connect(db)
    save_prices(conn, "AAA", [("2026-01-02", 95.0)])
    conn.close()
    main(["buylist", "--db", str(db), "status"])
    out = capsys.readouterr().out
    assert "AAA" in out and "TRIGGERED" in out


def test_cli_buylist_remove(tmp_path, capsys):
    db = tmp_path / "t.db"
    main(["buylist", "--db", str(db), "set", "--ticker", "AAA", "--target", "100"])
    capsys.readouterr()
    main(["buylist", "--db", str(db), "remove", "--ticker", "AAA"])
    assert "removed AAA" in capsys.readouterr().out
    main(["buylist", "--db", str(db), "status"])
    assert "empty" in capsys.readouterr().out


# ---- analyse --generator llm (slice 27) ----------------------------------


def test_cli_analyse_llm_generator(tmp_path, capsys, monkeypatch):
    # Patch the real client construction with a fake so the CLI path is hermetic.
    from types import SimpleNamespace

    import traders.llm_thesis as llm_thesis

    tool_input = {
        "actionable": True,
        "thesis_type": "value",
        "direction": "long",
        "conviction": 4,
        "suggested_size_pct": 3.0,
        "exit_condition": "re-rate to peers or a -8% stop",
        "rationale": "cheap on cash flow",
    }

    def fake_resolve(self):
        def create(**kwargs):
            block = SimpleNamespace(type="tool_use", input=tool_input)
            return SimpleNamespace(content=[block])

        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setattr(llm_thesis.LLMThesisGenerator, "_resolve_client", fake_resolve)

    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db)])
    capsys.readouterr()
    main(["analyse", "--db", str(db), "--generator", "llm"])
    out = capsys.readouterr().out
    assert "generator: llm" in out
    assert "1 thesis" in out
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT thesis_type, direction, rationale FROM theses").fetchone()
    conn.close()
    assert row[0] == "value"
    assert row[1] == "long"
    assert row[2].startswith("[llm]")


# ---- review --generator llm (slice 28) -----------------------------------


def test_cli_review_llm_generator(tmp_path, capsys, monkeypatch):
    from types import SimpleNamespace

    import traders.llm_postmortem as llm_pm

    tool_input = {"outcome": "AAA +20% as the thesis expected.", "lessons": "size winners larger"}

    def fake_resolve(self):
        def create(**kwargs):
            block = SimpleNamespace(type="tool_use", input=tool_input)
            return SimpleNamespace(content=[block])

        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setattr(llm_pm.LLMPostMortemGenerator, "_resolve_client", fake_resolve)

    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db)])
    main(["analyse", "--db", str(db)])
    conn = sqlite3.connect(db)
    thesis_id = conn.execute("SELECT id FROM theses LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO positions"
        " (ticker, thesis_id, opened_at, closed_at, entry_price, exit_price, size_pct, status)"
        " VALUES ('AAA', ?, '2026-01-01', '2026-02-01', 100.0, 120.0, 2.0, 'closed')",
        (thesis_id,),
    )
    conn.commit()
    conn.close()
    capsys.readouterr()
    main(["review", "--db", str(db), "--generator", "llm"])
    out = capsys.readouterr().out
    assert "generator: llm" in out
    assert "1 post-mortem" in out
    conn = sqlite3.connect(db)
    lessons = conn.execute("SELECT lessons FROM post_mortems").fetchone()[0]
    conn.close()
    assert lessons.startswith("[llm]")


# ---- eval-llm + missing-extra polish (slice 29) --------------------------


def _patch_llm_clients(monkeypatch, thesis_input, pm_input):
    from types import SimpleNamespace

    import traders.llm_postmortem as llm_pm
    import traders.llm_thesis as llm_thesis

    def _resolver(tool_input):
        def fake(self):
            def create(**kwargs):
                block = SimpleNamespace(type="tool_use", input=tool_input)
                return SimpleNamespace(content=[block])

            return SimpleNamespace(messages=SimpleNamespace(create=create))

        return fake

    monkeypatch.setattr(llm_thesis.LLMThesisGenerator, "_resolve_client", _resolver(thesis_input))
    monkeypatch.setattr(llm_pm.LLMPostMortemGenerator, "_resolve_client", _resolver(pm_input))


_THESIS_INPUT = {
    "actionable": True,
    "thesis_type": "value",
    "direction": "long",
    "conviction": 4,
    "suggested_size_pct": 3.0,
    "exit_condition": "re-rate to peers",
    "rationale": "cheap on cash flow and assets",
}
_PM_INPUT = {"outcome": "WIN ran up and LOSS faded, as scored.", "lessons": "size winners larger"}


def test_cli_eval_llm_runs(capsys, monkeypatch):
    _patch_llm_clients(monkeypatch, _THESIS_INPUT, _PM_INPUT)
    main(["eval-llm", "--generator", "both"])
    out = capsys.readouterr().out
    assert "LLM eval" in out
    assert "thesis generator" in out
    assert "post-mortem generator" in out


def test_cli_eval_llm_min_pass_rate_gate(capsys, monkeypatch):
    # The thesis fake answers every case with a thesis, so the "decline" golden
    # case fails -> pass rate < 1.0 -> the 1.0 gate exits non-zero.
    _patch_llm_clients(monkeypatch, _THESIS_INPUT, _PM_INPUT)
    with pytest.raises(SystemExit) as exc:
        main(["eval-llm", "--generator", "thesis", "--min-pass-rate", "1.0"])
    assert exc.value.code == 1
    assert "below threshold" in capsys.readouterr().out


def test_cli_eval_llm_missing_extra_exits_cleanly():
    import importlib.util

    if importlib.util.find_spec("anthropic") is not None:
        pytest.skip("anthropic installed — missing-extra path can't be exercised")
    with pytest.raises(SystemExit) as exc:
        main(["eval-llm", "--generator", "thesis"])
    assert "uv sync --extra llm" in str(exc.value)


def test_cli_analyse_llm_missing_extra_exits_cleanly(tmp_path, capsys):
    import importlib.util

    if importlib.util.find_spec("anthropic") is not None:
        pytest.skip("anthropic installed — missing-extra path can't be exercised")
    db = tmp_path / "t.db"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAA"], "eurostoxx50": []}))
    main(["scout", "--db", str(db), "--watchlist", str(wl), "--batch-size", "1"])
    main(["research", "--db", str(db)])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        main(["analyse", "--db", str(db), "--generator", "llm"])
    assert "uv sync --extra llm" in str(exc.value)  # clean message, not a traceback


# ---- jobs CLI (slice 32) -------------------------------------------------


def test_cli_jobs_status_enable_disable_check(tmp_path, capsys):
    dd = ["--data-dir", str(tmp_path)]
    main(["jobs", *dd, "status"])
    assert "[on ] daily" in capsys.readouterr().out  # default enabled

    main(["jobs", *dd, "disable", "daily"])
    assert "disabled daily" in capsys.readouterr().out

    with pytest.raises(SystemExit) as e_off:
        main(["jobs", *dd, "check", "daily"])
    assert e_off.value.code == 1  # disabled -> non-zero (cron script skips)
    with pytest.raises(SystemExit) as e_on:
        main(["jobs", *dd, "check", "weekly"])
    assert e_on.value.code == 0  # still enabled

    capsys.readouterr()
    main(["jobs", *dd, "status"])
    assert "[off] daily" in capsys.readouterr().out


def test_cli_jobs_unknown_exits_nonzero(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["jobs", "--data-dir", str(tmp_path), "enable", "bogus"])
    assert exc.value.code != 0
    assert "unknown job" in capsys.readouterr().out
