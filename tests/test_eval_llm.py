"""Tests for the LLM eval harness (slice 29).

Hermetic: the harness is pure (no anthropic). Tests drive it with fake
generators, and the one integration test injects a fake client into a real
LLM generator — so nothing imports anthropic or hits the network.
"""

from __future__ import annotations

from types import SimpleNamespace

from traders.eval_llm import (
    Check,
    PostMortemEvalCase,
    ThesisEvalCase,
    default_post_mortem_cases,
    default_thesis_cases,
    evaluate_post_mortem_generator,
    evaluate_thesis_generator,
    render_report,
)
from traders.post_mortems import ClosedPosition, PostMortemDraft, ThesisContext
from traders.signals import DraftThesis

_POS = ClosedPosition(
    position_id=1,
    ticker="AAA",
    thesis_id=1,
    direction="long",
    opened_at="2026-01-01",
    closed_at="2026-02-01",
    entry_price=100.0,
    exit_price=120.0,
    size_pct=3.0,
)
_THESIS = ThesisContext(
    thesis_id=1,
    thesis_type="value",
    direction="long",
    conviction=4,
    suggested_size_pct=3.0,
    exit_condition="re-rate",
    rationale="cheap on cash flow",
)


def _draft(thesis_type: str = "value", conviction: int = 4) -> DraftThesis:
    return DraftThesis(
        thesis_type, "long", conviction, 3.0, "exit", "a sufficiently long rationale here"
    )


class _FakeThesisGen:
    def __init__(self, mapping: dict[str, list[DraftThesis]]) -> None:
        self._m = mapping

    def generate(self, ticker: str, content: str) -> list[DraftThesis]:
        return self._m.get(ticker, [])


class _FakePMGen:
    def __init__(self, draft: PostMortemDraft) -> None:
        self._d = draft

    def generate(self, position, thesis) -> PostMortemDraft:
        return self._d


# ---- scoring ---------------------------------------------------------------


def test_all_checks_pass_is_ok():
    gen = _FakeThesisGen({"AAA": [_draft()], "BBB": []})
    cases = [
        ThesisEvalCase(
            "aaa-has-thesis",
            "AAA",
            "note",
            [
                Check("produces one thesis", lambda d: len(d) == 1),
                Check("is value", lambda d: d[0].thesis_type == "value"),
            ],
        ),
        ThesisEvalCase("bbb-declines", "BBB", "note", [Check("declines", lambda d: d == [])]),
    ]
    report = evaluate_thesis_generator(gen, cases)
    assert report.case_count == 2
    assert report.cases_ok == 2
    assert report.ok
    assert report.pass_rate == 1.0


def test_failed_check_is_recorded():
    gen = _FakeThesisGen({"AAA": [_draft(thesis_type="momentum")]})
    cases = [
        ThesisEvalCase(
            "c",
            "AAA",
            "n",
            [
                Check("is value", lambda d: d[0].thesis_type == "value"),
                Check("is long", lambda d: d[0].direction == "long"),
            ],
        )
    ]
    report = evaluate_thesis_generator(gen, cases)
    res = report.results[0]
    assert res.passed == 1
    assert res.total == 2
    assert not res.ok
    assert "is value" in res.failures
    assert report.pass_rate == 0.5
    assert not report.ok


def test_check_that_raises_counts_as_fail_not_crash():
    gen = _FakeThesisGen({"AAA": []})  # empty -> d[0] raises IndexError
    cases = [ThesisEvalCase("c", "AAA", "n", [Check("indexes", lambda d: d[0].conviction == 4)])]
    report = evaluate_thesis_generator(gen, cases)
    assert report.results[0].passed == 0
    assert not report.ok


def test_post_mortem_eval_scores():
    gen = _FakePMGen(PostMortemDraft(outcome="AAA +20% (win)", lessons="[llm] size winners up"))
    cases = [
        PostMortemEvalCase(
            "c",
            _POS,
            _THESIS,
            [
                Check("outcome mentions ticker", lambda pm: "AAA" in pm.outcome),
                Check("lessons non-empty", lambda pm: len(pm.lessons) > 0),
            ],
        )
    ]
    report = evaluate_post_mortem_generator(gen, cases)
    assert report.ok
    assert report.pass_rate == 1.0


# ---- default golden cases --------------------------------------------------


def test_default_cases_are_well_formed():
    tcases = default_thesis_cases()
    pcases = default_post_mortem_cases()
    assert tcases and all(c.checks for c in tcases)
    assert pcases and all(c.checks for c in pcases)


# ---- integration: harness drives a real LLM generator (fake client) --------


def test_harness_runs_against_llm_generator_with_fake_client():
    from traders.llm_thesis import LLMThesisGenerator

    tool_input = {
        "actionable": True,
        "thesis_type": "value",
        "direction": "long",
        "conviction": 4,
        "suggested_size_pct": 3.0,
        "exit_condition": "re-rate",
        "rationale": "cheap on cash flow",
    }

    def create(**kwargs):
        block = SimpleNamespace(type="tool_use", input=tool_input)
        return SimpleNamespace(content=[block])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    gen = LLMThesisGenerator(client=client)
    cases = [
        ThesisEvalCase(
            "value",
            "AAA",
            "a cheap, cash-generative name",
            [Check("value long", lambda d: bool(d) and d[0].thesis_type == "value")],
        )
    ]
    assert evaluate_thesis_generator(gen, cases).ok


# ---- rendering -------------------------------------------------------------


def test_render_report_text_and_markdown():
    gen = _FakeThesisGen({"AAA": [_draft()]})
    cases = [ThesisEvalCase("c", "AAA", "n", [Check("has thesis", lambda d: len(d) == 1)])]
    report = evaluate_thesis_generator(gen, cases)
    text = render_report(report, title="thesis", fmt="text")
    assert "LLM eval" in text
    assert "thesis" in text
    md = render_report(report, title="thesis", fmt="markdown")
    assert md.startswith("#")
    assert "thesis" in md
