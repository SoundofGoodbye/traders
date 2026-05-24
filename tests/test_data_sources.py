from traders.data_sources import DataPoint, StubDataSource


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
