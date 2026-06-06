"""CLI subcommand registry.

Each command module exposes a ``COMMANDS`` list of :class:`Command` records.
``all_commands`` concatenates them in the original subparser-registration order
so ``traders --help`` stays byte-for-byte stable after the decomposition.
"""

from __future__ import annotations

from traders.cli_commands import analysis, data, ops, pipeline
from traders.cli_commands._common import Command

# The original monolithic cli.py registered subparsers in exactly this order.
_ORDER = [
    "scout",
    "research",
    "analyse",
    "pm",
    "review",
    "run-daily",
    "run-weekly",
    "params",
    "jobs",
    "buylist",
    "universe",
    "capital-allocation",
    "exposure",
    "metrics",
    "optimize",
    "backtest",
    "ingest-prices",
    "ingest-fundamentals",
    "ingest-fundamental-periods",
    "eval-llm",
    "web",
    "feedback",
]


def all_commands() -> list[Command]:
    """Return every CLI command in the original subparser-registration order."""
    by_name = {c.name: c for m in (pipeline, analysis, data, ops) for c in m.COMMANDS}
    return [by_name[n] for n in _ORDER]
