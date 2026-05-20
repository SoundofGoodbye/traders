import json

from traders.scout import load_watchlist


def test_load_watchlist_returns_nonempty_list():
    tickers = load_watchlist()
    assert isinstance(tickers, list)
    assert len(tickers) >= 100
    assert all(isinstance(t, str) and t for t in tickers)


def test_load_watchlist_known_tickers():
    tickers = load_watchlist()
    assert "AAPL" in tickers
    assert "SAP.DE" in tickers


def test_load_watchlist_dedupes(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"sp100": ["A", "B", "A"], "eurostoxx50": ["B", "C"]}))
    assert load_watchlist(p) == ["A", "B", "C"]
