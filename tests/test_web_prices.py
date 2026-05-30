"""Tests for the optional current-price lookup used on the positions page."""

import pytest

from traders.web import prices


def test_price_fn_for_source_none_for_sources_without_quotes():
    assert prices.price_fn_for_source("stub") is None
    assert prices.price_fn_for_source("edgar") is None
    assert prices.price_fn_for_source("anything-else") is None


def test_price_fn_for_source_yfinance_returns_callable():
    pytest.importorskip("yfinance")
    fn = prices.price_fn_for_source("yfinance")
    assert callable(fn)


def test_yfinance_price_fn_reads_first_positive_key(monkeypatch):
    yf = pytest.importorskip("yfinance")

    class FakeTicker:
        def __init__(self, ticker):
            pass

        @property
        def info(self):
            # currentPrice missing/zero → falls through to regularMarketPrice
            return {"currentPrice": 0, "regularMarketPrice": 123.5}

    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    fn = prices.price_fn_for_source("yfinance")
    assert fn("AAA") == 123.5


def test_yfinance_price_fn_returns_none_on_non_dict_info(monkeypatch):
    yf = pytest.importorskip("yfinance")

    class FakeTicker:
        def __init__(self, ticker):
            pass

        @property
        def info(self):
            return "not a dict"

    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    fn = prices.price_fn_for_source("yfinance")
    assert fn("AAA") is None


def test_yfinance_price_fn_returns_none_when_no_usable_price(monkeypatch):
    yf = pytest.importorskip("yfinance")

    class FakeTicker:
        def __init__(self, ticker):
            pass

        @property
        def info(self):
            return {"currentPrice": None, "regularMarketPrice": -1, "previousClose": 0}

    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    fn = prices.price_fn_for_source("yfinance")
    assert fn("AAA") is None


def test_yfinance_price_fn_swallows_exceptions(monkeypatch):
    yf = pytest.importorskip("yfinance")

    class FakeTicker:
        def __init__(self, ticker):
            raise RuntimeError("network down")

    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    fn = prices.price_fn_for_source("yfinance")
    assert fn("AAA") is None  # a flaky ticker can't kill the page
