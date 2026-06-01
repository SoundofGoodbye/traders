"""Post-mortem abstractions for the Reviewer.

The Reviewer should never know whether the post-mortem text came from a
deterministic stub, a future LLM, or a quant model. v1 ships a
deterministic stub; a real generator drops in behind the same
`PostMortemGenerator` protocol in a later slice without touching the
Reviewer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ClosedPosition:
    """The subset of `positions` columns the Reviewer hands to a generator."""

    position_id: int
    ticker: str
    thesis_id: int
    direction: str
    opened_at: str
    closed_at: str
    entry_price: float | None
    exit_price: float | None
    size_pct: float


@dataclass(frozen=True)
class ThesisContext:
    """The subset of `theses` columns the Reviewer hands to a generator."""

    thesis_id: int
    thesis_type: str
    direction: str
    conviction: int
    suggested_size_pct: float
    exit_condition: str | None
    rationale: str | None


@dataclass(frozen=True)
class PostMortemDraft:
    """A post-mortem before it is persisted to `post_mortems`."""

    outcome: str
    lessons: str


def compute_pnl_pct(
    direction: str, entry_price: float | None, exit_price: float | None
) -> float | None:
    """Direction-aware PnL %. Returns None when prices are missing."""
    if entry_price is None or exit_price is None or entry_price == 0:
        return None
    if direction == "short":
        return (entry_price - exit_price) / entry_price * 100.0
    return (exit_price - entry_price) / entry_price * 100.0


class PostMortemGenerator(Protocol):
    """Produce a post-mortem for one closed position + its thesis."""

    def generate(self, position: ClosedPosition, thesis: ThesisContext) -> PostMortemDraft: ...


class StubPostMortemGenerator:
    """Deterministic stub. PnL from prices, lessons are canned text.

    Outcome stays deterministic-per-position so reports are stable
    across re-renders. Real, LLM-driven lessons land in a later slice
    behind the same protocol.
    """

    def generate(self, position: ClosedPosition, thesis: ThesisContext) -> PostMortemDraft:
        pnl = compute_pnl_pct(thesis.direction, position.entry_price, position.exit_price)
        if pnl is None:
            result = "unknown"
            outcome = (
                f"{position.ticker} {thesis.direction}: outcome unknown — "
                f"missing price data (entry={position.entry_price}, "
                f"exit={position.exit_price})."
            )
        else:
            result = "win" if pnl > 0 else "loss" if pnl < 0 else "flat"
            outcome = (
                f"{position.ticker} {thesis.direction}: {pnl:+.2f}% ({result}). "
                f"Entry {position.entry_price}, exit {position.exit_price}, "
                f"size {position.size_pct:.1f}%."
            )
        lessons = (
            f"[stub] {thesis.thesis_type} thesis at conviction "
            f"{thesis.conviction} closed as {result}. Real lessons land "
            "in a later slice."
        )
        return PostMortemDraft(outcome=outcome, lessons=lessons)
