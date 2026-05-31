"""Tests for strategy-goal loading."""

from __future__ import annotations

import json
from pathlib import Path

from traders.strategy import StrategyGoal, load_strategy


def test_loads_packaged_default_when_no_path_given():
    goal = load_strategy()
    assert isinstance(goal, StrategyGoal)
    assert goal.min_closed_for_verdict >= 1
    assert goal.target_return_pct_30d > 0


def test_loads_explicit_path(tmp_path: Path):
    payload = {
        "name": "aggressive",
        "description": "test goal",
        "target_return_pct_30d": 8.0,
        "max_drawdown_pct": 15.0,
        "min_hit_rate": 0.55,
        "min_sharpe": 1.5,
        "min_closed_for_verdict": 20,
    }
    p = tmp_path / "strategy.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    goal = load_strategy(p)

    assert goal.name == "aggressive"
    assert goal.target_return_pct_30d == 8.0
    assert goal.max_drawdown_pct == 15.0
    assert goal.min_hit_rate == 0.55
    assert goal.min_sharpe == 1.5
    assert goal.min_closed_for_verdict == 20
