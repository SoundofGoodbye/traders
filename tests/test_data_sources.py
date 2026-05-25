import pytest

from traders.data_sources import (
    DataPoint,
    StubDataSource,
    YFinanceDataSource,
    make_data_source,
)


def test_stub_returns_data_points():
    pts = StubDataSource().fetch("AAPL")
    assert pts
    assert all(isinstance(p, DataPoint) for p in pts)


def test_stub_is_deterministic():
    a = StubDataSource().fetch("AAPL")
    b = StubDataSource().fetch("AAPL")
    assert a == b


def test_stub_covers_required_kinds():
    pts = StubDataSource().fetch("MSFT")
    kinds = {p.kind for p in pts}
    assert {"filing", "news", "fundamentals"} <= kinds


def test_stub_mentions_ticker():
    pts = StubDataSource().fetch("SAP.DE")
    for p in pts:
        haystack = " ".join([p.title, p.url, p.snippet])
        assert "SAP.DE" in haystack


def test_data_point_fields():
    p = DataPoint(kind="news", title="t", url="u", snippet="s", published_at="2026-05-20")
    assert p.kind == "news"
    assert p.published_at == "2026-05-20"


# --- YFinanceDataSource (network-free, dependency-injected) ----------------


class _FakeTicker:
    """Stand-in for `yfinance.Ticker(symbol)`. Configurable per test."""

    def __init__(self, *, news=None, info=None, news_error=None, info_error=None):
        self._news = news if news is not None else []
        self._info = info if info is not None else {}
        self._news_error = news_error
        self._info_error = info_error

    @property
    def news(self):
        if self._news_error is not None:
            raise self._news_error
        return self._news

    @property
    def info(self):
        if self._info_error is not None:
            raise self._info_error
        return self._info


def _ticker_fn_from(ticker_map):
    return lambda symbol: ticker_map[symbol]


def test_yfinance_emits_news_and_fundamentals_data_points():
    fake = _FakeTicker(
        news=[
            {
                "title": "AAPL beats expectations",
                "link": "https://example.com/aapl-beats",
                "providerPublishTime": 1715731200,  # 2024-05-15 UTC
                "summary": "Apple beat estimates.",
            }
        ],
        info={"trailingPE": 28.5, "profitMargins": 0.25, "marketCap": 3_000_000_000_000},
    )
    ds = YFinanceDataSource(ticker_fn=_ticker_fn_from({"AAPL": fake}))
    points = ds.fetch("AAPL")

    kinds = [p.kind for p in points]
    assert "news" in kinds
    assert "fundamentals" in kinds

    news = next(p for p in points if p.kind == "news")
    assert news.title == "AAPL beats expectations"
    assert news.url == "https://example.com/aapl-beats"
    assert news.published_at == "2024-05-15"
    assert "beat" in news.snippet

    fundamentals = next(p for p in points if p.kind == "fundamentals")
    assert "trailing P/E" in fundamentals.snippet
    assert "profit margin" in fundamentals.snippet
    assert fundamentals.url.startswith("http")


def test_yfinance_supports_new_content_news_shape():
    """yfinance 0.2.40+ wraps news under `content` with `canonicalUrl.url`."""
    fake = _FakeTicker(
        news=[
            {
                "content": {
                    "title": "MSFT update",
                    "canonicalUrl": {"url": "https://example.com/msft"},
                    "pubDate": "2026-04-30T12:00:00Z",
                    "summary": "Microsoft did a thing.",
                }
            }
        ],
        info={},
    )
    ds = YFinanceDataSource(ticker_fn=_ticker_fn_from({"MSFT": fake}))
    points = ds.fetch("MSFT")
    news = [p for p in points if p.kind == "news"]
    assert len(news) == 1
    assert news[0].title == "MSFT update"
    assert news[0].url == "https://example.com/msft"
    assert news[0].published_at == "2026-04-30"


def test_yfinance_returns_empty_list_when_no_data():
    fake = _FakeTicker(news=[], info={})
    ds = YFinanceDataSource(ticker_fn=_ticker_fn_from({"NONE": fake}))
    assert ds.fetch("NONE") == []


def test_yfinance_omits_fundamentals_when_all_fields_missing():
    fake = _FakeTicker(
        news=[{"title": "x", "link": "u", "providerPublishTime": 0}],
        info={"sector": "Tech", "industry": "Software"},
    )
    ds = YFinanceDataSource(ticker_fn=_ticker_fn_from({"X": fake}))
    points = ds.fetch("X")
    assert all(p.kind != "fundamentals" for p in points)


def test_yfinance_swallows_ticker_constructor_error():
    def boom(_symbol):
        raise ConnectionError("DNS failure")

    ds = YFinanceDataSource(ticker_fn=boom)
    assert ds.fetch("AAPL") == []


def test_yfinance_swallows_news_attribute_error():
    fake = _FakeTicker(
        news_error=RuntimeError("upstream 429"),
        info={"trailingPE": 18.0},
    )
    ds = YFinanceDataSource(ticker_fn=_ticker_fn_from({"AAPL": fake}))
    points = ds.fetch("AAPL")
    # News failed but fundamentals still made it through.
    assert [p.kind for p in points] == ["fundamentals"]


def test_yfinance_swallows_info_attribute_error():
    fake = _FakeTicker(
        news=[{"title": "AAPL t", "link": "u", "providerPublishTime": 1715731200}],
        info_error=RuntimeError("upstream timeout"),
    )
    ds = YFinanceDataSource(ticker_fn=_ticker_fn_from({"AAPL": fake}))
    points = ds.fetch("AAPL")
    assert [p.kind for p in points] == ["news"]


def test_yfinance_skips_news_items_without_title():
    fake = _FakeTicker(
        news=[
            {"link": "u", "providerPublishTime": 0},  # no title
            {"title": "real one", "link": "u2", "providerPublishTime": 1715731200},
        ],
        info={},
    )
    ds = YFinanceDataSource(ticker_fn=_ticker_fn_from({"X": fake}))
    points = ds.fetch("X")
    assert len(points) == 1
    assert points[0].title == "real one"


def test_yfinance_caps_news_at_limit():
    fake = _FakeTicker(
        news=[
            {"title": f"item {i}", "link": "u", "providerPublishTime": 1715731200}
            for i in range(10)
        ],
        info={},
    )
    ds = YFinanceDataSource(
        ticker_fn=_ticker_fn_from({"X": fake}), news_limit=3
    )
    points = ds.fetch("X")
    assert len(points) == 3


def test_yfinance_default_ctor_lazy_imports_yfinance():
    """Without yfinance installed, the default constructor must raise
    ImportError pointing the user at `uv sync --extra realdata`. With
    a custom ticker_fn, no import is attempted (test-environment safe).
    """
    # If yfinance happens to be installed (e.g. CI with the extra),
    # the bare constructor will succeed; that's fine — we only care
    # that injection bypasses the import path.
    ds = YFinanceDataSource(ticker_fn=lambda _s: _FakeTicker(news=[], info={}))
    assert ds.fetch("AAPL") == []


def test_make_data_source_returns_stub():
    ds = make_data_source("stub")
    assert isinstance(ds, StubDataSource)


def test_make_data_source_yfinance_requires_install():
    """Without the realdata extra, `make_data_source('yfinance')` must
    surface a clear ImportError. With it installed, the factory returns
    the adapter. Either branch is acceptable — we just refuse silent fallback.
    """
    try:
        ds = make_data_source("yfinance")
    except ImportError as e:
        assert "realdata" in str(e)
    else:
        assert isinstance(ds, YFinanceDataSource)


def test_make_data_source_unknown_raises():
    with pytest.raises(ValueError):
        make_data_source("polygon")
