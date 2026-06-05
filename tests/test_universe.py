"""Tests for universe coverage classification (slice 45 / B11)."""

from __future__ import annotations

import json

from traders.universe import classify, classify_watchlist


def test_classify_splits_us_from_foreign_venue():
    report = classify(["AAPL", "MC.PA", "BRK.B", "SAP.DE"], source="tiingo")
    assert report.total == 4
    assert "AAPL" in report.priceable and "BRK.B" in report.priceable
    assert "MC.PA" in report.skipped and "SAP.DE" in report.skipped  # foreign venues


def test_classify_unknown_source_raises():
    try:
        classify(["AAPL"], source="bogus")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "bogus" in str(e)


def test_classify_watchlist_reads_json(tmp_path):
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["AAPL", "MSFT"], "eurostoxx50": ["MC.PA"]}))
    report = classify_watchlist(wl, source="tiingo")
    assert report.total == 3
    assert set(report.priceable) == {"AAPL", "MSFT"}
    assert report.skipped == ["MC.PA"]
