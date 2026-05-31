"""Learned parameters — the tunable knobs agents read each run.

The video's "Cornelius tunes the learned_parameters JSON every week."
This module owns that file: the agent thresholds that an optimizer
(slice 16) can rewrite without touching code.

Scoped to the knobs that actually exist in the pipeline today:

* ``batch_size`` — how many candidates the Scout surfaces per run.
* ``max_total_size_pct`` — the PM's total-exposure cap (% of NAV).

(The Analyst has no numeric knob of its own yet — its conviction and
sizing come from the thesis generator. More knobs slot into this
dataclass as real generators/signals land.)

Layered load, same convention as ``traders.strategy``: an operator/
optimizer override at ``data/learned_parameters.json`` (gitignored
runtime dir) wins, else the packaged ``parameters.default.json`` whose
values equal the previous in-code defaults, so behavior is unchanged
until something deliberately tunes them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

_PACKAGED_DEFAULT = (
    Path(__file__).resolve().parent / "data" / "parameters.default.json"
)
_OVERRIDE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "learned_parameters.json"
)


@dataclass(frozen=True)
class LearnedParameters:
    """Tunable agent knobs. Defaults equal the original in-code constants."""

    batch_size: int = 10
    max_total_size_pct: float = 20.0


def parameter_names() -> tuple[str, ...]:
    """The tunable field names, for the optimizer to pick a knob from."""
    return tuple(f.name for f in fields(LearnedParameters))


def _from_dict(data: dict) -> LearnedParameters:
    kwargs = {}
    for f in fields(LearnedParameters):
        if f.name not in data:
            continue
        kwargs[f.name] = int(data[f.name]) if f.type == "int" else float(data[f.name])
    return LearnedParameters(**kwargs)


def load_parameters(path: Path | None = None) -> LearnedParameters:
    """Load active parameters.

    With no explicit ``path``: use ``data/learned_parameters.json`` if it
    exists, else the packaged default. Tests pass an explicit path to
    stay hermetic.
    """
    if path is not None:
        return _from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    source = _OVERRIDE if _OVERRIDE.exists() else _PACKAGED_DEFAULT
    return _from_dict(json.loads(source.read_text(encoding="utf-8")))


def override_path() -> Path:
    """The live, mutable parameters file the optimizer writes to."""
    return _OVERRIDE


def save_parameters(params: LearnedParameters, path: Path | None = None) -> Path:
    """Persist parameters as JSON. Defaults to the live override path."""
    target = Path(path) if path is not None else _OVERRIDE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(params), indent=2) + "\n", encoding="utf-8")
    return target
