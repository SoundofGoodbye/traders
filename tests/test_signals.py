from traders.signals import THESIS_TYPES, DraftThesis, StubThesisGenerator


def test_stub_returns_draft_theses():
    drafts = StubThesisGenerator().generate("AAPL", "some content")
    assert drafts
    assert all(isinstance(d, DraftThesis) for d in drafts)


def test_stub_is_deterministic():
    a = StubThesisGenerator().generate("AAPL", "x")
    b = StubThesisGenerator().generate("AAPL", "x")
    assert a == b


def test_stub_thesis_type_is_valid():
    for ticker in ("AAPL", "MSFT", "SAP.DE", "ZZZZ"):
        drafts = StubThesisGenerator().generate(ticker, "x")
        for d in drafts:
            assert d.thesis_type in THESIS_TYPES


def test_stub_conviction_in_range():
    drafts = StubThesisGenerator().generate("AAPL", "x")
    for d in drafts:
        assert 1 <= d.conviction <= 5


def test_stub_mentions_ticker_in_rationale():
    drafts = StubThesisGenerator().generate("SAP.DE", "x")
    assert all("SAP.DE" in d.rationale for d in drafts)


def test_draft_thesis_fields():
    d = DraftThesis(
        thesis_type="value",
        direction="long",
        conviction=3,
        suggested_size_pct=2.0,
        exit_condition="exit",
        rationale="why",
    )
    assert d.thesis_type == "value"
    assert d.conviction == 3
