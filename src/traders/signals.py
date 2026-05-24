"""Thesis-generation abstractions for the Analyst.

The Analyst should never know whether the thesis came from a heuristic
stub, a future LLM, or a quant model. v1 ships a deterministic stub; a
real generator drops in behind the same `ThesisGenerator` protocol in
a later slice without touching the Analyst.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

THESIS_TYPES = ("value", "catalyst", "momentum", "mean-reversion")
DIRECTIONS = ("long", "short")


@dataclass(frozen=True)
class DraftThesis:
    """A candidate thesis before it is persisted to `theses`."""

    thesis_type: str
    direction: str
    conviction: int
    suggested_size_pct: float
    exit_condition: str
    rationale: str


class ThesisGenerator(Protocol):
    """Produce zero-or-more theses for a researched candidate.

    Implementations return an empty list rather than raising when a
    ticker yields no actionable thesis, so the Analyst can still record
    that the candidate was considered.
    """

    def generate(self, ticker: str, content: str) -> list[DraftThesis]: ...


class StubThesisGenerator:
    """Canned, deterministic-per-ticker thesis. No I/O, no model.

    Picks a thesis type by hashing the ticker so different candidates
    in the same run get different `thesis_type` values — useful for
    eyeballing reports until a real generator lands.
    """

    def generate(self, ticker: str, content: str) -> list[DraftThesis]:
        idx = sum(ord(c) for c in ticker) % len(THESIS_TYPES)
        thesis_type = THESIS_TYPES[idx]
        return [
            DraftThesis(
                thesis_type=thesis_type,
                direction="long",
                conviction=3,
                suggested_size_pct=2.0,
                exit_condition="Re-evaluate after next quarterly earnings.",
                rationale=(
                    f"[stub] Baseline {thesis_type} thesis for {ticker}; "
                    "real generator lands in a later slice."
                ),
            )
        ]
