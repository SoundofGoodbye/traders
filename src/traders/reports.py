"""Markdown renderers for the daily PM report and the weekly Reviewer run.

Pure transformation layer: takes already-persisted data (a `DailyReport`
in memory, or post-mortem rows out of SQLite) and produces a markdown
document the user can read, save, or hand to a downstream tool.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from traders.metrics import ScoreCard
from traders.portfolio import DailyReport, ReportItem
from traders.post_mortems import compute_pnl_pct


@dataclass(frozen=True)
class ReviewItem:
    """One row's worth of post-mortem context for the weekly report."""

    post_mortem_id: int
    position_id: int
    ticker: str
    direction: str
    thesis_type: str
    conviction: int
    size_pct: float
    entry_price: float | None
    exit_price: float | None
    opened_at: str
    closed_at: str
    outcome: str
    lessons: str


def _format_item(item: ReportItem) -> str:
    return (
        f"- **{item.ticker}** — {item.thesis_type}, {item.direction}, "
        f"conviction {item.conviction}, size {item.suggested_size_pct:.1f}% "
        f"(thesis {item.thesis_id}): {item.reason}"
    )


def render_daily_report_markdown(report: DailyReport) -> str:
    """Render the PM's daily report as a markdown document."""
    if report.pm_run_id == 0:
        return "# Daily Report\n\n_No analyst run available yet — nothing to evaluate._"
    lines = [
        f"# Daily Report — PM run {report.pm_run_id}",
        "",
        f"Analyst run: {report.analyst_run_id}  ",
        f"Accepted: {len(report.accepted)}  ",
        f"Rejected: {len(report.rejected)}",
        "",
        "## Accepted",
        "",
    ]
    if report.accepted:
        lines.extend(_format_item(i) for i in report.accepted)
    else:
        lines.append("_None._")
    lines.extend(["", "## Rejected", ""])
    if report.rejected:
        lines.extend(_format_item(i) for i in report.rejected)
    else:
        lines.append("_None._")
    return "\n".join(lines) + "\n"


def _format_review_item(item: ReviewItem) -> list[str]:
    pnl = compute_pnl_pct(item.direction, item.entry_price, item.exit_price)
    pnl_line = f"PnL: {pnl:+.2f}%" if pnl is not None else "PnL: unknown (missing prices)"
    return [
        f"### {item.ticker} — {item.thesis_type}, {item.direction} (position {item.position_id})",
        "",
        f"- Opened {item.opened_at}, closed {item.closed_at}",
        f"- Entry {item.entry_price}, exit {item.exit_price}, size {item.size_pct:.1f}%",
        f"- Conviction {item.conviction}",
        f"- {pnl_line}",
        "",
        f"**Outcome:** {item.outcome}",
        "",
        f"**Lessons:** {item.lessons}",
        "",
    ]


def render_weekly_review_markdown(
    items: list[ReviewItem], reviewer_run_id: int | None = None
) -> str:
    """Render a weekly review (one reviewer run's post-mortems) as markdown."""
    if reviewer_run_id is None or reviewer_run_id == 0 or not items:
        header = "# Weekly Review"
        if reviewer_run_id:
            header = f"# Weekly Review — reviewer run {reviewer_run_id}"
        return f"{header}\n\n_No post-mortems in this run._\n"
    lines = [
        f"# Weekly Review — reviewer run {reviewer_run_id}",
        "",
        f"Post-mortems: {len(items)}",
        "",
    ]
    for item in items:
        lines.extend(_format_review_item(item))
    return "\n".join(lines).rstrip() + "\n"


def latest_reviewer_run_id(conn: sqlite3.Connection) -> int | None:
    """Most recent reviewer_run_id in `post_mortems`, or None if empty."""
    row = conn.execute("SELECT MAX(reviewer_run_id) FROM post_mortems").fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def load_review_for_run(conn: sqlite3.Connection, reviewer_run_id: int) -> list[ReviewItem]:
    """Load one reviewer run's post-mortems joined with positions + theses."""
    rows = conn.execute(
        "SELECT pm.id, pm.position_id, p.ticker, t.direction, t.thesis_type,"
        " t.conviction, p.size_pct, p.entry_price, p.exit_price,"
        " p.opened_at, p.closed_at, pm.outcome, pm.lessons"
        " FROM post_mortems pm"
        " JOIN positions p ON p.id = pm.position_id"
        " JOIN theses t ON t.id = p.thesis_id"
        " WHERE pm.reviewer_run_id = ?"
        " ORDER BY pm.id",
        (reviewer_run_id,),
    ).fetchall()
    return [
        ReviewItem(
            post_mortem_id=int(r[0]),
            position_id=int(r[1]),
            ticker=r[2],
            direction=r[3],
            thesis_type=r[4],
            conviction=int(r[5]),
            size_pct=float(r[6]),
            entry_price=None if r[7] is None else float(r[7]),
            exit_price=None if r[8] is None else float(r[8]),
            opened_at=r[9],
            closed_at=r[10],
            outcome=r[11],
            lessons=r[12],
        )
        for r in rows
    ]


def _fmt_value(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_metrics(card: ScoreCard, fmt: str = "text") -> str:
    """Render a strategy scorecard as text or markdown."""
    if fmt == "markdown":
        return _metrics_markdown(card)
    return _metrics_text(card)


def _metrics_text(card: ScoreCard) -> str:
    lines = [
        f"Strategy scorecard — verdict: {card.verdict.upper()}",
        f"  closed positions: {card.metrics.num_closed}",
    ]
    for c in card.criteria:
        flag = "PASS" if c.passed else "FAIL"
        lines.append(
            f"  {c.name}: {_fmt_value(c.value)} (threshold {c.threshold:.2f}) {flag}"
        )
    return "\n".join(lines)


def _metrics_markdown(card: ScoreCard) -> str:
    lines = [
        f"# Strategy Scorecard — {card.verdict.replace('_', ' ').title()}",
        "",
        f"**Closed positions:** {card.metrics.num_closed}",
        "",
        "| Criterion | Value | Threshold | Result |",
        "| --- | --- | --- | --- |",
    ]
    for c in card.criteria:
        flag = "✅" if c.passed else "❌"
        lines.append(
            f"| {c.name} | {_fmt_value(c.value)} | {c.threshold:.2f} | {flag} |"
        )
    return "\n".join(lines)


def render_backtest(result, fmt: str = "text") -> str:
    """Render a backtest result (replay summary + scorecard) as text or markdown."""
    if fmt == "markdown":
        head = [
            f"# Backtest — {result.scorecard.verdict.replace('_', ' ').title()}",
            "",
            f"**Window:** {result.start} → {result.end} "
            f"({result.rebalance_count} rebalances, {result.holding_days}d hold)  ",
            f"**Params:** batch_size={result.batch_size}, "
            f"max_total_size_pct={result.max_total_size_pct} "
            f"({result.strategy} strategy)  ",
            f"**Trades:** {result.num_trades} "
            f"(entry-skipped {result.skipped_no_price}, "
            f"unpriced {result.unpriced_trades})",
            "",
        ]
        return "\n".join(head) + render_metrics(result.scorecard, "markdown") + "\n"
    lines = [
        f"Backtest — verdict: {result.scorecard.verdict.upper()}",
        f"  window: {result.start} -> {result.end} "
        f"({result.rebalance_count} rebalances, hold {result.holding_days}d)",
        f"  params: batch_size={result.batch_size}, "
        f"max_total_size_pct={result.max_total_size_pct} ({result.strategy} strategy)",
        f"  trades: {result.num_trades} "
        f"(entry-skipped {result.skipped_no_price}, "
        f"unpriced {result.unpriced_trades})",
    ]
    for c in result.scorecard.criteria:
        flag = "PASS" if c.passed else "FAIL"
        lines.append(
            f"  {c.name}: {_fmt_value(c.value)} (threshold {c.threshold:.2f}) {flag}"
        )
    return "\n".join(lines)


_COMPARE_ROWS = (
    ("verdict", lambda r: r.scorecard.verdict),
    ("trades", lambda r: r.num_trades),
    ("return_pct_30d", lambda r: _fmt_value(r.metrics.return_pct_30d)),
    ("total_pnl_pct", lambda r: _fmt_value(r.metrics.total_pnl_pct)),
    ("hit_rate", lambda r: _fmt_value(r.metrics.hit_rate)),
    ("max_drawdown_pct", lambda r: _fmt_value(r.metrics.max_drawdown_pct)),
    ("sharpe_per_trade", lambda r: _fmt_value(r.metrics.sharpe_per_trade)),
)


def render_backtest_comparison(baseline, candidate, fmt: str = "text") -> str:
    """Render a baseline-vs-candidate backtest side by side (text or markdown)."""
    if fmt == "markdown":
        lines = [
            "# Backtest Comparison",
            "",
            f"Window {baseline.start} → {baseline.end}, "
            f"{baseline.holding_days}d hold.",
            "",
            "| Metric | Baseline | Candidate |",
            "| --- | --- | --- |",
        ]
        for name, getter in _COMPARE_ROWS:
            lines.append(f"| {name} | {getter(baseline)} | {getter(candidate)} |")
        return "\n".join(lines) + "\n"
    lines = [
        "Backtest comparison "
        f"({baseline.start} -> {baseline.end}, hold {baseline.holding_days}d)",
        f"  params: baseline batch={baseline.batch_size}/"
        f"cap={baseline.max_total_size_pct} vs candidate batch="
        f"{candidate.batch_size}/cap={candidate.max_total_size_pct}",
    ]
    for name, getter in _COMPARE_ROWS:
        lines.append(f"  {name}: {getter(baseline)} -> {getter(candidate)}")
    return "\n".join(lines)
