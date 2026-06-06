"""Unit tests for the shared SEC EDGAR HTTP helpers (audit D3)."""

import pytest

from traders import edgar_http


def test_require_ua_raises_when_unset(monkeypatch):
    monkeypatch.delenv("TRADERS_EDGAR_UA", raising=False)
    with pytest.raises(RuntimeError, match="TRADERS_EDGAR_UA"):
        edgar_http.require_ua()


def test_require_ua_returns_value(monkeypatch):
    monkeypatch.setenv("TRADERS_EDGAR_UA", "Tester test@example.com")
    assert edgar_http.require_ua() == "Tester test@example.com"


def test_load_cik_map_builds_upper_padded(monkeypatch):
    payload = {
        "0": {"ticker": "aapl", "cik_str": 320193},
        "1": {"ticker": "MSFT", "cik_str": 789019},
        "2": {"missing": "fields"},  # skipped: no ticker/cik
    }
    monkeypatch.setattr(edgar_http, "get_json", lambda url, ua, timeout=10.0: payload)
    assert edgar_http.load_cik_map("ua") == {"AAPL": "0000320193", "MSFT": "0000789019"}
