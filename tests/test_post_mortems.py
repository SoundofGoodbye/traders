from traders.post_mortems import (
    ClosedPosition,
    PostMortemDraft,
    StubPostMortemGenerator,
    ThesisContext,
    compute_pnl_pct,
)


def _pos(direction="long", entry=100.0, exit_=110.0):
    return ClosedPosition(
        position_id=1,
        ticker="AAA",
        thesis_id=1,
        direction=direction,
        opened_at="2026-05-01",
        closed_at="2026-05-20",
        entry_price=entry,
        exit_price=exit_,
        size_pct=2.0,
    )


def _thesis(thesis_type="value", direction="long", conviction=3):
    return ThesisContext(
        thesis_id=1,
        thesis_type=thesis_type,
        direction=direction,
        conviction=conviction,
        suggested_size_pct=2.0,
        exit_condition="re-eval next earnings",
        rationale="because",
    )


def test_pnl_long_win():
    assert compute_pnl_pct("long", 100.0, 110.0) == 10.0


def test_pnl_long_loss():
    assert compute_pnl_pct("long", 100.0, 90.0) == -10.0


def test_pnl_short_win():
    assert compute_pnl_pct("short", 100.0, 90.0) == 10.0


def test_pnl_short_loss():
    assert compute_pnl_pct("short", 100.0, 110.0) == -10.0


def test_pnl_flat():
    assert compute_pnl_pct("long", 100.0, 100.0) == 0.0


def test_pnl_missing_prices():
    assert compute_pnl_pct("long", None, 100.0) is None
    assert compute_pnl_pct("long", 100.0, None) is None


def test_pnl_zero_entry_returns_none():
    assert compute_pnl_pct("long", 0.0, 10.0) is None


def test_stub_generator_long_win():
    gen = StubPostMortemGenerator()
    draft = gen.generate(
        _pos(direction="long", entry=100.0, exit_=110.0),
        _thesis(direction="long"),
    )
    assert isinstance(draft, PostMortemDraft)
    assert "AAA" in draft.outcome
    assert "+10.00%" in draft.outcome
    assert "win" in draft.outcome
    assert "[stub]" in draft.lessons


def test_stub_generator_long_loss():
    gen = StubPostMortemGenerator()
    draft = gen.generate(
        _pos(direction="long", entry=100.0, exit_=90.0),
        _thesis(direction="long"),
    )
    assert "-10.00%" in draft.outcome
    assert "loss" in draft.outcome


def test_stub_generator_short_win():
    gen = StubPostMortemGenerator()
    draft = gen.generate(
        _pos(direction="short", entry=100.0, exit_=90.0),
        _thesis(direction="short"),
    )
    assert "+10.00%" in draft.outcome
    assert "win" in draft.outcome


def test_stub_generator_short_loss():
    gen = StubPostMortemGenerator()
    draft = gen.generate(
        _pos(direction="short", entry=100.0, exit_=110.0),
        _thesis(direction="short"),
    )
    assert "-10.00%" in draft.outcome
    assert "loss" in draft.outcome


def test_stub_generator_missing_prices():
    gen = StubPostMortemGenerator()
    draft = gen.generate(
        _pos(entry=None, exit_=None),
        _thesis(),
    )
    assert "unknown" in draft.outcome.lower()
    assert "[stub]" in draft.lessons


def test_stub_generator_mentions_thesis_type_and_conviction():
    gen = StubPostMortemGenerator()
    draft = gen.generate(
        _pos(),
        _thesis(thesis_type="catalyst", conviction=5),
    )
    assert "catalyst" in draft.lessons
    assert "5" in draft.lessons
