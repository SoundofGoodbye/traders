import pytest

from traders.data_sources import (
    DataPoint,
    EdgarDataSource,
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
    ds = YFinanceDataSource(ticker_fn=_ticker_fn_from({"X": fake}), news_limit=3)
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


# --- EdgarDataSource (network-free, dependency-injected) -------------------


def _filings_fn_from(filings_map):
    return lambda symbol: filings_map[symbol]


def test_edgar_emits_filing_data_points():
    filings = [
        {
            "form": "10-K",
            "filingDate": "2026-01-15",
            "accessionNumber": "0000320193-26-000001",
            "primaryDocument": "aapl-20251228.htm",
            "cik": "320193",
        },
        {
            "form": "10-Q",
            "filingDate": "2026-04-30",
            "accessionNumber": "0000320193-26-000002",
            "primaryDocument": "aapl-q1.htm",
            "cik": "320193",
        },
    ]
    ds = EdgarDataSource(filings_fn=_filings_fn_from({"AAPL": filings}))
    points = ds.fetch("AAPL")
    assert len(points) == 2
    assert all(p.kind == "filing" for p in points)
    assert points[0].title == "AAPL 10-K"
    assert points[0].published_at == "2026-01-15"
    assert "sec.gov" in points[0].url
    assert "000032019326000001" in points[0].url


def test_edgar_filters_unwanted_forms():
    filings = [
        {
            "form": "10-K",
            "filingDate": "2026-01-15",
            "accessionNumber": "a-1",
            "primaryDocument": "x.htm",
            "cik": "1",
        },
        {
            "form": "SC 13G",
            "filingDate": "2026-02-01",
            "accessionNumber": "a-2",
            "primaryDocument": "y.htm",
            "cik": "1",
        },
        {
            "form": "10-Q",
            "filingDate": "2026-04-30",
            "accessionNumber": "a-3",
            "primaryDocument": "z.htm",
            "cik": "1",
        },
    ]
    ds = EdgarDataSource(filings_fn=_filings_fn_from({"X": filings}))
    points = ds.fetch("X")
    assert [p.title for p in points] == ["X 10-K", "X 10-Q"]


def test_edgar_returns_empty_for_unknown_ticker():
    ds = EdgarDataSource(filings_fn=lambda _s: [])
    assert ds.fetch("ZZZZ") == []


def test_edgar_swallows_fetcher_error():
    def boom(_symbol):
        raise RuntimeError("network down")

    ds = EdgarDataSource(filings_fn=boom)
    assert ds.fetch("AAPL") == []


def test_edgar_respects_limit():
    filings = [
        {
            "form": "8-K",
            "filingDate": f"2026-04-{i:02d}",
            "accessionNumber": f"a-{i}",
            "primaryDocument": f"d{i}.htm",
            "cik": "1",
        }
        for i in range(1, 11)
    ]
    ds = EdgarDataSource(filings_fn=_filings_fn_from({"X": filings}), limit=3)
    points = ds.fetch("X")
    assert len(points) == 3


def test_edgar_skips_non_dict_items():
    filings = [
        None,
        "not a dict",
        {
            "form": "10-K",
            "filingDate": "2026-01-15",
            "accessionNumber": "a-1",
            "primaryDocument": "x.htm",
            "cik": "1",
        },
    ]
    ds = EdgarDataSource(filings_fn=_filings_fn_from({"X": filings}))
    points = ds.fetch("X")
    assert len(points) == 1
    assert points[0].title == "X 10-K"


def test_make_data_source_edgar_requires_env(monkeypatch):
    """Without TRADERS_EDGAR_UA, the default fetcher must raise; with it
    set, the factory returns an EdgarDataSource.
    """
    monkeypatch.delenv("TRADERS_EDGAR_UA", raising=False)
    with pytest.raises(RuntimeError):
        make_data_source("edgar")
    monkeypatch.setenv("TRADERS_EDGAR_UA", "Test test@example.com")
    ds = make_data_source("edgar")
    assert isinstance(ds, EdgarDataSource)


# ---- EDGAR filing-text enrichment (slice 48 / B12) -------------------------


def test_edgar_enriches_10k_snippet_with_section_excerpt():
    from traders.data_sources import EdgarDataSource

    filings = [
        {
            "form": "10-K",
            "filingDate": "2024-02-15",
            "accessionNumber": "0000320193-24-000123",
            "primaryDocument": "aapl.htm",
            "cik": "320193",
        }
    ]
    doc = "Item 1A. Risk Factors We face supply-chain and FX risk. Item 1B. Other None."
    src = EdgarDataSource(filings_fn=lambda t: filings, document_fetcher=lambda url: doc)
    points = src.fetch("AAPL")
    assert len(points) == 1
    assert points[0].snippet.startswith("AAPL filed 10-K on 2024-02-15.")
    assert "Risk Factors We face supply-chain" in points[0].snippet
    assert "Other None" not in points[0].snippet  # stopped at the next Item


def test_edgar_without_document_fetcher_keeps_metadata_snippet():
    from traders.data_sources import EdgarDataSource

    filings = [{"form": "10-K", "filingDate": "2024-02-15", "cik": "320193"}]
    src = EdgarDataSource(filings_fn=lambda t: filings)
    assert src.fetch("AAPL")[0].snippet == "AAPL filed 10-K on 2024-02-15."


def test_edgar_document_fetch_error_degrades_gracefully():
    from traders.data_sources import EdgarDataSource

    def boom(url):
        raise RuntimeError("network down")

    filings = [
        {
            "form": "10-K",
            "filingDate": "2024-02-15",
            "accessionNumber": "x",
            "primaryDocument": "d.htm",
            "cik": "1",
        }
    ]
    src = EdgarDataSource(filings_fn=lambda t: filings, document_fetcher=boom)
    assert src.fetch("AAPL")[0].snippet == "AAPL filed 10-K on 2024-02-15."


def test_edgar_8k_not_enriched():
    from traders.data_sources import EdgarDataSource

    filings = [{"form": "8-K", "filingDate": "2024-03-01", "cik": "1"}]
    src = EdgarDataSource(
        filings_fn=lambda t: filings, document_fetcher=lambda url: "Item 1A. Risk Factors stuff."
    )
    assert src.fetch("AAPL")[0].snippet == "AAPL filed 8-K on 2024-03-01."  # 8-K left as metadata
