"""Strategy goal — the numeric definition of success and failure.

The video's framing: "define what success and failure look like — it's
not vibes, it's numbers." This module loads those numbers.

Layered load, mirroring how the rest of the system treats config vs
state: an operator-editable override at ``data/strategy.json`` (gitignored
runtime dir) wins; otherwise the packaged ``strategy.default.json`` is
used. JSON, not YAML — the core install has zero dependencies.

Pure file read. No DB, no network. The scorer lives in
``traders.metrics`` so this module stays a plain config object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_PACKAGED_DEFAULT = Path(__file__).resolve().parent / "data" / "strategy.default.json"
_OVERRIDE = Path(__file__).resolve().parent.parent.parent / "data" / "strategy.json"


@dataclass(frozen=True)
class StrategyGoal:
    """Success/failure thresholds the scorer measures realized results against."""

    name: str
    description: str
    target_return_pct_30d: float
    max_drawdown_pct: float
    min_hit_rate: float
    min_sharpe: float
    min_closed_for_verdict: int


def _from_dict(data: dict) -> StrategyGoal:
    return StrategyGoal(
        name=str(data.get("name", "default")),
        description=str(data.get("description", "")),
        target_return_pct_30d=float(data["target_return_pct_30d"]),
        max_drawdown_pct=float(data["max_drawdown_pct"]),
        min_hit_rate=float(data["min_hit_rate"]),
        min_sharpe=float(data["min_sharpe"]),
        min_closed_for_verdict=int(data["min_closed_for_verdict"]),
    )


def load_strategy(path: Path | None = None) -> StrategyGoal:
    """Load the active goal.

    With no explicit ``path``: use ``data/strategy.json`` if it exists,
    else the packaged default. Tests pass an explicit path to stay
    hermetic.
    """
    if path is not None:
        return _from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    source = _OVERRIDE if _OVERRIDE.exists() else _PACKAGED_DEFAULT
    return _from_dict(json.loads(source.read_text(encoding="utf-8")))
