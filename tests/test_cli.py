import json
import sqlite3

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
