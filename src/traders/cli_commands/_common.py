"""Shared helpers and the Command registry type for CLI subcommands.

Command modules live alongside this one. Each builds its subparser(s) exactly
as the old monolithic ``cli.py`` did and dispatches the same body, then exposes a
``COMMANDS`` list of :class:`Command` records. ``cli_commands.all_commands``
concatenates them in subparser-registration order so ``--help`` stays stable.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Command:
    """One CLI subcommand: its name, subparser builder, and dispatch body."""

    name: str
    add_parser: Callable[[argparse._SubParsersAction], None]
    run: Callable[[argparse.Namespace], None]


def _emit(text: str, output: Path | None) -> None:
    """Write `text` to `output` (creating its parent dir) or to stdout."""
    if output is None:
        print(text, end="" if text.endswith("\n") else "\n")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    print(f"wrote {output}")


@contextmanager
def _llm_extra_guard(active: bool, conn=None):
    """Turn a missing-'llm'-extra ImportError into a clean CLI exit.

    Only converts when ``active`` (an LLM generator was selected), so an unrelated
    ImportError elsewhere isn't masked. Closes ``conn`` (if given) before exiting.
    """
    try:
        yield
    except ImportError as e:
        if not active:
            raise
        if conn is not None:
            conn.close()
        raise SystemExit(str(e)) from e


def _apply_with_gate(conn, args) -> None:
    """Apply a proposed experiment, gated on out-of-sample improvement (slice 24).

    Refuses the apply unless the candidate beats baseline out-of-sample *and* its
    deflated Sharpe survives the number of proposals tried — unless ``--force`` is
    given. The human ``--apply`` gate is unchanged; this only withholds the
    recommendation, and ``--force`` restores the pre-slice-24 unconditional apply.
    """
    from datetime import date as _date
    from datetime import timedelta as _timedelta

    from traders.backtest import (
        DEFAULT_MIN_DSR,
        DEFAULT_OOS_FRACTION,
        gate_experiment,
    )
    from traders.optimizer import OptimizerError, apply_experiment, get_experiment
    from traders.parameters import load_parameters
    from traders.prices import load_history_from_db, synthetic_history
    from traders.reports import render_experiment_gate
    from traders.scout import load_watchlist
    from traders.strategy import load_strategy

    if args.force:
        exp = apply_experiment(conn, args.apply, params_path=args.params)
        print(
            f"applied experiment {exp.id}: {exp.param} -> {exp.new_value} "
            "(gate bypassed via --force)"
        )
        return

    # Fail fast on a non-proposed id before running the (potentially slow) gate.
    exp_row = get_experiment(conn, args.apply)
    if exp_row is None:
        raise OptimizerError(f"no experiment with id={args.apply}")
    if exp_row.status != "proposed":
        raise OptimizerError(f"experiment {args.apply} is {exp_row.status}, not proposed")

    end = _date.fromisoformat(args.end) if args.end else _date.today()
    start = _date.fromisoformat(args.start) if args.start else end - _timedelta(days=365)
    oos_fraction = args.oos_fraction if args.oos_fraction is not None else DEFAULT_OOS_FRACTION
    min_dsr = args.min_dsr if args.min_dsr is not None else DEFAULT_MIN_DSR
    watchlist = load_watchlist(args.watchlist)
    baseline = (
        load_parameters(args.params)
        if args.params is not None and Path(args.params).exists()
        else load_parameters()
    )

    if args.source == "db":
        # Load all available history so signals get their pre-window lookback.
        history = load_history_from_db(conn, tickers=watchlist)
    else:
        print(
            "warning: gating on the synthetic price source — illustrative only; "
            "do not trust this verdict for a real decision."
        )
        history = synthetic_history(watchlist, start - _timedelta(days=420), end)

    gate_kwargs = dict(
        goal=load_strategy(),
        start=start,
        end=end,
        oos_fraction=oos_fraction,
        min_dsr=min_dsr,
        params=baseline,
        watchlist=watchlist,
        use_signals=(args.strategy == "signals"),
    )
    if args.holding_days is not None:
        gate_kwargs["holding_days"] = args.holding_days
    if args.rebalance_days is not None:
        gate_kwargs["rebalance_every_days"] = args.rebalance_days

    gate = gate_experiment(conn, args.apply, history, **gate_kwargs)
    _emit(render_experiment_gate(gate, fmt=args.fmt), args.output)
    if gate.passed:
        exp = apply_experiment(conn, args.apply, params_path=args.params)
        print(
            f"applied experiment {exp.id}: {exp.param} -> {exp.new_value} "
            "(passed the out-of-sample gate)"
        )
    else:
        print(
            "not applied — the proposal did not pass the out-of-sample gate. "
            "Review the report above, then re-run with --force to override."
        )
