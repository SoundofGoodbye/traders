"""Self-improvement loop — propose, gate, and apply one change at a time.

This is the part of the video that actually matters, in this repo's
terms: read the realized outcomes (slice 14), propose **one**
single-variable change to the learned parameters (slice 15), log it to
an append-only ledger, and let a human approve it before anything moves.
The "first cycle is review-only; flip to live when ready" behavior is
exactly the proposed -> applied gate here.

Following the scientific method: exactly one knob changes per
experiment, so the next scorecard attributes any movement to that knob.
v1 ships a deterministic `StubOptimizer`; a model-driven optimizer drops
in behind the same `Optimizer` protocol without touching this module —
the same pattern as `PostMortemGenerator` and `ThesisGenerator`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from traders.metrics import ScoreCard, compute_and_score
from traders.parameters import LearnedParameters, load_parameters, save_parameters
from traders.strategy import StrategyGoal, load_strategy

# Bounds and steps for the stub's single-variable moves.
_EXPOSURE_FLOOR = 2.0
_EXPOSURE_CEIL = 100.0
_EXPOSURE_DOWN = 0.8
_EXPOSURE_UP = 1.2
_BATCH_STEP = 2


class OptimizerError(ValueError):
    """Raised when an experiment can't be applied or rejected as asked."""


@dataclass(frozen=True)
class Proposal:
    """A single-variable change the optimizer wants to try."""

    param: str
    old_value: float
    new_value: float
    hypothesis: str


@dataclass(frozen=True)
class Experiment:
    """One persisted experiment row."""

    id: int
    created_at: str
    hypothesis: str
    param: str
    old_value: str
    new_value: str
    baseline_verdict: str
    status: str
    decided_at: str | None


class Optimizer(Protocol):
    """Propose zero-or-one change given a scorecard and current params."""

    def propose(self, scorecard: ScoreCard, params: LearnedParameters) -> Proposal | None: ...


class StubOptimizer:
    """Deterministic rule-based optimizer. One knob per proposal.

    Only proposes when the strategy is `failing` — `on_track` needs no
    change and `insufficient_data` means there isn't enough evidence to
    learn from yet. The failing criterion picks the knob.
    """

    def propose(self, scorecard: ScoreCard, params: LearnedParameters) -> Proposal | None:
        if scorecard.verdict != "failing":
            return None
        failing = {c.name for c in scorecard.criteria if not c.passed}
        if "max_drawdown_pct" in failing:
            new = round(max(_EXPOSURE_FLOOR, params.max_total_size_pct * _EXPOSURE_DOWN), 1)
            return self._exposure(params, new, "drawdown exceeds the limit")
        if "return_pct_30d" in failing:
            new = round(min(_EXPOSURE_CEIL, params.max_total_size_pct * _EXPOSURE_UP), 1)
            return self._exposure(params, new, "trailing return is below target")
        if "hit_rate" in failing or "sharpe_per_trade" in failing:
            new = params.batch_size + _BATCH_STEP
            return Proposal(
                param="batch_size",
                old_value=params.batch_size,
                new_value=new,
                hypothesis=(
                    "hit-rate / risk-adjusted return is weak; surfacing more "
                    "candidates gives the PM a wider, higher-quality choice set"
                ),
            )
        return None

    def _exposure(self, params: LearnedParameters, new: float, why: str) -> Proposal | None:
        if new == params.max_total_size_pct:
            return None
        direction = "lower" if new < params.max_total_size_pct else "raise"
        return Proposal(
            param="max_total_size_pct",
            old_value=params.max_total_size_pct,
            new_value=new,
            hypothesis=f"{why}; {direction} the exposure cap and re-measure",
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_experiment(row: tuple) -> Experiment:
    return Experiment(
        id=int(row[0]),
        created_at=row[1],
        hypothesis=row[2],
        param=row[3],
        old_value=row[4],
        new_value=row[5],
        baseline_verdict=row[6],
        status=row[7],
        decided_at=row[8],
    )


_SELECT = (
    "SELECT id, created_at, hypothesis, param, old_value, new_value,"
    " baseline_verdict, status, decided_at FROM experiments"
)


def list_experiments(conn: sqlite3.Connection, status: str | None = None) -> list[Experiment]:
    """All experiments (optionally filtered by status), newest first."""
    if status is None:
        rows = conn.execute(f"{_SELECT} ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(f"{_SELECT} WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
    return [_row_to_experiment(r) for r in rows]


def get_experiment(conn: sqlite3.Connection, experiment_id: int) -> Experiment | None:
    row = conn.execute(f"{_SELECT} WHERE id = ?", (experiment_id,)).fetchone()
    return _row_to_experiment(row) if row is not None else None


def propose_experiment(
    conn: sqlite3.Connection,
    goal: StrategyGoal | None = None,
    params: LearnedParameters | None = None,
    optimizer: Optimizer | None = None,
) -> Experiment | None:
    """Score the strategy and log one proposal. Does not change params.

    Returns the existing open proposal if one is already pending (one
    open experiment at a time), the new proposal, or None when the
    optimizer has nothing to suggest.
    """
    pending = list_experiments(conn, status="proposed")
    if pending:
        return pending[0]
    goal = goal or load_strategy()
    params = params or load_parameters()
    opt: Optimizer = optimizer or StubOptimizer()
    card = compute_and_score(conn, goal)
    proposal = opt.propose(card, params)
    if proposal is None:
        return None
    cur = conn.execute(
        "INSERT INTO experiments (created_at, hypothesis, param, old_value,"
        " new_value, baseline_verdict, baseline_metrics, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed')",
        (
            _now(),
            proposal.hypothesis,
            proposal.param,
            str(proposal.old_value),
            str(proposal.new_value),
            card.verdict,
            json.dumps(asdict(card.metrics)),
        ),
    )
    conn.commit()
    return get_experiment(conn, int(cur.lastrowid))


def _cast(param: str, value: str) -> float:
    for f in fields(LearnedParameters):
        if f.name == param:
            return int(float(value)) if f.type == "int" else float(value)
    raise OptimizerError(f"unknown parameter {param!r}")


def _require_proposed(conn: sqlite3.Connection, experiment_id: int) -> Experiment:
    exp = get_experiment(conn, experiment_id)
    if exp is None:
        raise OptimizerError(f"no experiment with id={experiment_id}")
    if exp.status != "proposed":
        raise OptimizerError(f"experiment {experiment_id} is {exp.status}, not proposed")
    return exp


def apply_experiment(
    conn: sqlite3.Connection,
    experiment_id: int,
    params_path: Path | None = None,
) -> Experiment:
    """Apply a proposed experiment: write the one change, mark it applied."""
    exp = _require_proposed(conn, experiment_id)
    if params_path is not None and Path(params_path).exists():
        current = load_parameters(params_path)
    else:
        current = load_parameters()
    updated = replace(current, **{exp.param: _cast(exp.param, exp.new_value)})
    save_parameters(updated, params_path)
    conn.execute(
        "UPDATE experiments SET status = 'applied', decided_at = ? WHERE id = ?",
        (_now(), experiment_id),
    )
    conn.commit()
    return get_experiment(conn, experiment_id)  # type: ignore[return-value]


def reject_experiment(conn: sqlite3.Connection, experiment_id: int) -> Experiment:
    """Reject a proposed experiment. Parameters are left untouched."""
    _require_proposed(conn, experiment_id)
    conn.execute(
        "UPDATE experiments SET status = 'rejected', decided_at = ? WHERE id = ?",
        (_now(), experiment_id),
    )
    conn.commit()
    return get_experiment(conn, experiment_id)  # type: ignore[return-value]
