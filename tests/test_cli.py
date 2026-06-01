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
    main(["ingest-prices", "--db", str(db), "--ticker", "FOO.ZZ"])
    out = capsys.readouterr().out
    assert "0 close(s)" in out
    assert "skipped: FOO.ZZ" in out


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
