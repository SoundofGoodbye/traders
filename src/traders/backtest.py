"""Backtest harness — replay a parameter set over historical prices.

Live paper-trading closes a few positions a week, far too sparse to converge the
slice-16 Optimizer one variable at a time. This harness replays the pipeline
over a ``PriceHistory`` so a parameter set is scored against months of data in
seconds.

It composes the *pure* decision primitives the live agents use —
``scout.filter_candidates``, the ``ThesisGenerator``, ``portfolio.evaluate``,
``post_mortems.compute_pnl_pct`` and ``metrics`` — so the simulated decisions
can't silently diverge from production. Nothing here touches the live tables:
the simulation runs entirely in memory and returns a value object.

Model: walk rebalance dates from ``start`` to ``end`` stepping
``rebalance_every_days``. At each date close any position whose holding period
has elapsed (at the as-of close), then select → generate → evaluate against
the still-open sim positions and open each accepted pick at the as-of close,
scheduling its exit ``holding_days`` later. After the window, realize whatever
is still open. Score the realized trades with the existing goal + metrics.

Selection/generation come in two modes. ``use_signals=False`` (default) replays
the rotation Scout + the passed ``generator`` (stub by default). ``use_signals=True``
replays the *real* signal strategy — ``scout.rank_candidates`` and
``SignalThesisGenerator`` recomputed as-of each rebalance date — so the harness
measures slices 20–21, not just the placeholder.

Assumptions (deliberate v1 simplifications — documented, not hidden):

* Entries fill at the same close used to make the decision — no slippage, and a
  mild fill-timing optimism versus a next-session fill. An entry lag is a future
  refinement.
* Trades are equal-weighted; ``conviction`` / ``thesis_type`` ride along on each
  trade but do not weight PnL — matching the live ``metrics``, which are
  price-only. Size only governs *which* picks the exposure cap admits.
* ``synthetic_history`` is illustrative (a bounded random walk), not calibrated
  market data — use it to compare parameter sets, not to set absolute thresholds.
* Watchlist / rebalance / batch-size interplay drives coverage (which names are
  ever picked); a degenerate combination can bias the sample.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from traders.metrics import ClosedTrade, Metrics, ScoreCard, metrics_from_trades, score
from traders.parameters import LearnedParameters, load_parameters
from traders.portfolio import OpenPosition, ThesisRow, evaluate
from traders.post_mortems import compute_pnl_pct
from traders.prices import PriceHistory
from traders.scout import filter_candidates, load_watchlist, rank_candidates
from traders.signals import StubThesisGenerator, ThesisGenerator
from traders.signals_thesis import SignalThesisGenerator
from traders.strategy import StrategyGoal, load_strategy

DEFAULT_HOLDING_DAYS = 21
DEFAULT_REBALANCE_DAYS = 7


class BacktestError(ValueError):
    """Raised when a backtest can't be set up as requested."""


@dataclass(frozen=True)
class BacktestTrade:
    """One realized round-trip in the simulation."""

    ticker: str
    direction: str
    thesis_type: str
    conviction: int
    size_pct: float
    entry_day: str
    entry_price: float
    exit_day: str
    exit_price: float | None
    pnl_pct: float | None


@dataclass(frozen=True)
class BacktestResult:
    """A scored replay over one price history + parameter set."""

    start: str
    end: str
    holding_days: int
    rebalance_every_days: int
    batch_size: int
    max_total_size_pct: float
    strategy: str
    cost_bps: float
    entry_lag_days: int
    rebalance_count: int
    num_trades: int
    skipped_no_price: int
    unpriced_trades: int
    metrics: Metrics
    scorecard: ScoreCard
    trades: tuple[BacktestTrade, ...]


@dataclass(frozen=True)
class _SimPosition:
    ticker: str
    direction: str
    thesis_type: str
    conviction: int
    size_pct: float
    entry_day: date
    entry_price: float
    exit_day: date


def _rebalance_dates(start: date, end: date, step: int) -> list[date]:
    if step < 1:
        raise BacktestError(f"rebalance_every_days must be >= 1, got {step}")
    out: list[date] = []
    day = start
    while day <= end:
        out.append(day)
        day += timedelta(days=step)
    return out


def _realize(sim: _SimPosition, history: PriceHistory, cost_bps: float = 0.0) -> BacktestTrade:
    exit_price = history.close_asof(sim.ticker, sim.exit_day)
    pnl = compute_pnl_pct(sim.direction, sim.entry_price, exit_price)
    if pnl is not None and cost_bps:
        pnl -= cost_bps / 100.0  # round-trip cost: bps -> percent
    return BacktestTrade(
        ticker=sim.ticker,
        direction=sim.direction,
        thesis_type=sim.thesis_type,
        conviction=sim.conviction,
        size_pct=sim.size_pct,
        entry_day=sim.entry_day.isoformat(),
        entry_price=sim.entry_price,
        exit_day=sim.exit_day.isoformat(),
        exit_price=exit_price,
        pnl_pct=pnl,
    )


def run_backtest(
    history: PriceHistory,
    *,
    params: LearnedParameters,
    goal: StrategyGoal,
    start: date,
    end: date,
    holding_days: int = DEFAULT_HOLDING_DAYS,
    rebalance_every_days: int = DEFAULT_REBALANCE_DAYS,
    watchlist: list[str] | None = None,
    generator: ThesisGenerator | None = None,
    use_signals: bool = False,
    signal_kwargs: dict | None = None,
    cost_bps: float = 0.0,
    entry_lag_days: int = 0,
) -> BacktestResult:
    """Replay the pipeline over ``history`` with ``params`` and score it.

    With ``use_signals=True`` the harness replays the *signal* strategy — the
    same data-driven Scout ranking (``rank_candidates``) and ``SignalThesisGenerator``
    the live pipeline uses, recomputed as-of each rebalance date — so a backtest
    measures the real strategy, not the rotation+stub placeholder. The default
    (``False``) keeps the rotation+``generator`` behaviour.
    """
    if holding_days < 1:
        raise BacktestError(f"holding_days must be >= 1, got {holding_days}")
    if end < start:
        raise BacktestError(f"end {end} is before start {start}")
    wl = [t for t in (watchlist if watchlist is not None else load_watchlist()) if t]
    default_gen: ThesisGenerator = generator or StubThesisGenerator()
    skw = signal_kwargs or {}
    strategy = "signals" if use_signals else "rotation"

    open_pos: dict[str, _SimPosition] = {}
    closed: list[BacktestTrade] = []
    skipped = 0
    next_thesis_id = 0
    rebal = _rebalance_dates(start, end, rebalance_every_days)

    for today in rebal:
        # 1. Close positions whose holding period has elapsed.
        for ticker in [t for t, sp in open_pos.items() if sp.exit_day <= today]:
            closed.append(_realize(open_pos.pop(ticker), history, cost_bps))

        # 2. Decide — the exact live primitives, in order.
        if use_signals:
            picks = rank_candidates(wl, history, today, params.batch_size)
            gen = SignalThesisGenerator(history=history, as_of=today, **skw)
        else:
            picks = filter_candidates(wl, today, params.batch_size)
            gen = default_gen
        theses: list[ThesisRow] = []
        for ticker in picks:
            for draft in gen.generate(ticker, ""):
                next_thesis_id += 1
                theses.append(
                    ThesisRow(
                        thesis_id=next_thesis_id,
                        ticker=ticker,
                        thesis_type=draft.thesis_type,
                        direction=draft.direction,
                        conviction=draft.conviction,
                        suggested_size_pct=draft.suggested_size_pct,
                    )
                )
        held = [OpenPosition(ticker=sp.ticker, size_pct=sp.size_pct) for sp in open_pos.values()]
        accepted, _rejected = evaluate(theses, held, params.max_total_size_pct)

        # 3. Open each accepted pick at the as-of close.
        for item in accepted:
            entry_day = today + timedelta(days=entry_lag_days)
            entry = history.close_asof(item.ticker, entry_day)
            if entry is None:
                skipped += 1
                continue
            open_pos[item.ticker] = _SimPosition(
                ticker=item.ticker,
                direction=item.direction,
                thesis_type=item.thesis_type,
                conviction=item.conviction,
                size_pct=item.suggested_size_pct,
                entry_day=entry_day,
                entry_price=entry,
                exit_day=entry_day + timedelta(days=holding_days),
            )

    # Realize anything still open at its scheduled exit.
    for ticker in list(open_pos):
        closed.append(_realize(open_pos.pop(ticker), history, cost_bps))

    # Order oldest-close-first so drawdown is deterministic and meaningful.
    closed.sort(key=lambda t: (t.exit_day, t.entry_day, t.ticker))
    metric_trades = [
        ClosedTrade(t.ticker, t.direction, t.pnl_pct, t.exit_day)
        for t in closed
        if t.pnl_pct is not None
    ]
    metrics = metrics_from_trades(metric_trades)
    card = score(metrics, goal)
    # Trades that opened but couldn't be scored (no usable entry/exit price —
    # e.g. a zero or missing close in a sparse db history). Counted, not hidden.
    unpriced = len(closed) - len(metric_trades)

    return BacktestResult(
        start=start.isoformat(),
        end=end.isoformat(),
        holding_days=holding_days,
        rebalance_every_days=rebalance_every_days,
        batch_size=params.batch_size,
        max_total_size_pct=params.max_total_size_pct,
        strategy=strategy,
        cost_bps=cost_bps,
        entry_lag_days=entry_lag_days,
        rebalance_count=len(rebal),
        num_trades=len(closed),
        skipped_no_price=skipped,
        unpriced_trades=unpriced,
        metrics=metrics,
        scorecard=card,
        trades=tuple(closed),
    )


def split_backtest(
    history: PriceHistory,
    *,
    params: LearnedParameters,
    goal: StrategyGoal,
    start: date,
    end: date,
    oos_fraction: float = 0.3,
    **kwargs: object,
) -> tuple[BacktestResult, BacktestResult]:
    """Split the window into in-sample / out-of-sample and backtest each.

    The first ``1 - oos_fraction`` of the calendar span is in-sample; the rest is
    out-of-sample. A strategy that scores well in-sample but not out-of-sample is
    overfit — this is the core guardrail against tuning on the scoring history.
    """
    if not 0.0 < oos_fraction < 1.0:
        raise BacktestError(f"oos_fraction must be in (0, 1), got {oos_fraction}")
    span = (end - start).days
    if span < 2:
        raise BacktestError("window too short to split")
    split_day = start + timedelta(days=int(span * (1.0 - oos_fraction)))
    in_sample = run_backtest(
        history,
        params=params,
        goal=goal,
        start=start,
        end=split_day,
        **kwargs,  # type: ignore[arg-type]
    )
    out_sample = run_backtest(
        history,
        params=params,
        goal=goal,
        start=split_day + timedelta(days=1),
        end=end,
        **kwargs,  # type: ignore[arg-type]
    )
    return in_sample, out_sample


def compare_params(
    history: PriceHistory,
    *,
    baseline: LearnedParameters,
    candidate: LearnedParameters,
    goal: StrategyGoal,
    start: date,
    end: date,
    **kwargs: object,
) -> tuple[BacktestResult, BacktestResult]:
    """Run two parameter sets over the same history. Returns (baseline, candidate)."""
    base = run_backtest(history, params=baseline, goal=goal, start=start, end=end, **kwargs)  # type: ignore[arg-type]
    cand = run_backtest(history, params=candidate, goal=goal, start=start, end=end, **kwargs)  # type: ignore[arg-type]
    return base, cand


def _cast_param(param: str, value: str) -> float | int:
    fields = LearnedParameters.__dataclass_fields__
    if param not in fields:
        raise BacktestError(f"unknown parameter {param!r}")
    return int(float(value)) if fields[param].type == "int" else float(value)


def backtest_experiment(
    conn,
    experiment_id: int,
    history: PriceHistory,
    *,
    goal: StrategyGoal | None = None,
    start: date,
    end: date,
    params: LearnedParameters | None = None,
    **kwargs: object,
) -> tuple[BacktestResult, BacktestResult]:
    """Backtest a slice-16 experiment: current params vs its one proposed change.

    Returns (baseline, candidate) so a human can compare before applying. This
    is what makes the Optimizer's proposals testable against history instead of
    against a handful of live trades.
    """
    from traders.optimizer import get_experiment

    exp = get_experiment(conn, experiment_id)
    if exp is None:
        raise BacktestError(f"no experiment with id={experiment_id}")
    baseline = params or load_parameters()
    candidate = replace(baseline, **{exp.param: _cast_param(exp.param, exp.new_value)})
    return compare_params(
        history,
        baseline=baseline,
        candidate=candidate,
        goal=goal or load_strategy(),
        start=start,
        end=end,
        **kwargs,
    )
