"""Eval harness for the LLM generators (slice 29) — the loop that keeps them honest.

Scores an LLM generator's output against fixture cases so a prompt or model change
that quietly degrades quality is caught. Each case pairs an input with a list of
``Check`` predicates over the output; the harness runs the generator, applies the
checks, and aggregates an ``EvalReport`` (per-case pass/fail + an overall pass
rate). A check that raises is counted as a failure, never a crash.

Pure stdlib and generator-agnostic — it only needs the ``generate`` method of a
``ThesisGenerator`` / ``PostMortemGenerator``. So the harness itself imports no
``anthropic`` and is fully hermetic: tests drive it with fake generators (or a
real LLM generator wired to an injected fake client). Running the *default* cases
against the real Claude generators — the actual regression check — is the
``traders eval-llm`` CLI command, which needs the ``llm`` extra + an API key.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from traders.post_mortems import ClosedPosition, ThesisContext


@dataclass(frozen=True)
class Check:
    """A named predicate over a generator's output."""

    description: str
    predicate: Callable[[Any], bool]


@dataclass(frozen=True)
class ThesisEvalCase:
    """One thesis-generator eval: an input note + checks over ``list[DraftThesis]``."""

    name: str
    ticker: str
    note: str
    checks: list[Check]


@dataclass(frozen=True)
class PostMortemEvalCase:
    """One post-mortem eval: a closed position + checks over the ``PostMortemDraft``."""

    name: str
    position: ClosedPosition
    thesis: ThesisContext
    checks: list[Check]


@dataclass(frozen=True)
class CaseResult:
    """How one case scored."""

    name: str
    passed: int
    total: int
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.passed == self.total


@dataclass(frozen=True)
class EvalReport:
    """Aggregate of one eval run."""

    results: list[CaseResult]

    @property
    def case_count(self) -> int:
        return len(self.results)

    @property
    def cases_ok(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def total_checks(self) -> int:
        return sum(r.total for r in self.results)

    @property
    def passed_checks(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def pass_rate(self) -> float:
        return self.passed_checks / self.total_checks if self.total_checks else 1.0

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)


def _safe(predicate: Callable[[Any], bool], output: Any) -> bool:
    """Evaluate a check; a predicate that raises counts as a failure, not a crash."""
    try:
        return bool(predicate(output))
    except Exception:
        return False


def _score(name: str, output: Any, checks: Sequence[Check]) -> CaseResult:
    failures = [c.description for c in checks if not _safe(c.predicate, output)]
    return CaseResult(
        name=name, passed=len(checks) - len(failures), total=len(checks), failures=failures
    )


def evaluate_thesis_generator(generator, cases: Sequence[ThesisEvalCase]) -> EvalReport:
    """Run each thesis case through ``generator.generate(ticker, note)`` and score it."""
    return EvalReport(
        [_score(c.name, generator.generate(c.ticker, c.note), c.checks) for c in cases]
    )


def evaluate_post_mortem_generator(generator, cases: Sequence[PostMortemEvalCase]) -> EvalReport:
    """Run each post-mortem case through ``generator.generate(position, thesis)`` and score it."""
    return EvalReport(
        [_score(c.name, generator.generate(c.position, c.thesis), c.checks) for c in cases]
    )


# --- default golden cases ---------------------------------------------------
# Quality expectations for the real generators. Structural + light-semantic so
# they're meaningful without being brittle against normal model variation.


def default_thesis_cases() -> list[ThesisEvalCase]:
    valid_type = ("value", "catalyst", "momentum", "mean-reversion")
    return [
        ThesisEvalCase(
            name="clear-value",
            ticker="VALU",
            note=(
                "VALU trades at 7x earnings versus a 15x sector median, grew free "
                "cash flow 12% last year, carries little debt, and just authorized a "
                "buyback. No near-term catalyst, but the discount looks unwarranted."
            ),
            checks=[
                Check("produces a thesis", lambda d: len(d) >= 1),
                Check("long direction", lambda d: all(t.direction == "long" for t in d)),
                Check("valid thesis_type", lambda d: all(t.thesis_type in valid_type for t in d)),
                Check("conviction in 1..5", lambda d: all(1 <= t.conviction <= 5 for t in d)),
                Check("size in (0, 10]", lambda d: all(0 < t.suggested_size_pct <= 10 for t in d)),
                Check("non-trivial rationale", lambda d: all(len(t.rationale) >= 20 for t in d)),
            ],
        ),
        ThesisEvalCase(
            name="no-edge-declines",
            ticker="NOOP",
            note=(
                "NOOP is a mid-cap with no recent news, in-line results, valuation "
                "near its own history, and no obvious catalyst or signal either way."
            ),
            checks=[
                Check("declines (no actionable edge)", lambda d: len(d) == 0),
            ],
        ),
    ]


def default_post_mortem_cases() -> list[PostMortemEvalCase]:
    win = ClosedPosition(
        position_id=1,
        ticker="WIN",
        thesis_id=1,
        direction="long",
        opened_at="2026-01-05",
        closed_at="2026-02-05",
        entry_price=100.0,
        exit_price=125.0,
        size_pct=3.0,
    )
    win_thesis = ThesisContext(
        thesis_id=1,
        thesis_type="momentum",
        direction="long",
        conviction=4,
        suggested_size_pct=3.0,
        exit_condition="exit on 12-1 momentum turning negative",
        rationale="strong 12-1 momentum with confirming volume",
    )
    loss = ClosedPosition(
        position_id=2,
        ticker="LOSS",
        thesis_id=2,
        direction="long",
        opened_at="2026-01-10",
        closed_at="2026-02-10",
        entry_price=100.0,
        exit_price=82.0,
        size_pct=2.0,
    )
    loss_thesis = ThesisContext(
        thesis_id=2,
        thesis_type="value",
        direction="long",
        conviction=3,
        suggested_size_pct=2.0,
        exit_condition="re-rate to peers or a -8% stop",
        rationale="cheap on book value after a sell-off",
    )
    return [
        PostMortemEvalCase(
            name="winner",
            position=win,
            thesis=win_thesis,
            checks=[
                Check("outcome mentions ticker", lambda pm: "WIN" in pm.outcome),
                Check("outcome non-empty", lambda pm: len(pm.outcome.strip()) >= 10),
                Check("lessons non-empty", lambda pm: len(pm.lessons.strip()) >= 10),
            ],
        ),
        PostMortemEvalCase(
            name="loser",
            position=loss,
            thesis=loss_thesis,
            checks=[
                Check("outcome mentions ticker", lambda pm: "LOSS" in pm.outcome),
                Check("outcome non-empty", lambda pm: len(pm.outcome.strip()) >= 10),
                Check("lessons non-empty", lambda pm: len(pm.lessons.strip()) >= 10),
            ],
        ),
    ]


def render_report(report: EvalReport, title: str = "", fmt: str = "text") -> str:
    """Render an ``EvalReport`` as text or markdown."""
    label = f" — {title}" if title else ""
    rate = f"{report.pass_rate * 100:.0f}%"
    if fmt == "markdown":
        lines = [
            f"# LLM eval{label}",
            "",
            f"**{report.cases_ok}/{report.case_count} cases** · "
            f"{report.passed_checks}/{report.total_checks} checks · pass rate {rate}",
            "",
            "| Case | Checks | Result |",
            "| --- | --- | --- |",
        ]
        for r in report.results:
            flag = "✅" if r.ok else "❌"
            lines.append(f"| {r.name} | {r.passed}/{r.total} | {flag} |")
        for r in report.results:
            for f in r.failures:
                lines.append(f"\n- ❌ **{r.name}**: {f}")
        return "\n".join(lines) + "\n"
    lines = [
        f"LLM eval{label}: {report.cases_ok}/{report.case_count} cases, "
        f"{report.passed_checks}/{report.total_checks} checks (pass rate {rate})",
    ]
    for r in report.results:
        flag = "PASS" if r.ok else "FAIL"
        lines.append(f"  [{flag}] {r.name} ({r.passed}/{r.total})")
        for f in r.failures:
            lines.append(f"      - {f}")
    return "\n".join(lines)
