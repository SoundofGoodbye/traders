"""Thesis-intact monitoring — is the reason to *own the business* still true?

Backlog item B6 and the review's "is the thesis still intact?" point: exits today
are price/stop/time only, and nothing asks whether the premise behind a holding
has broken. ``thesis_intact`` reads the latest look-ahead-safe annual periods
(slice 33) for a name and flags business-level deterioration regardless of price —
lossmaking, cash burn, tight liquidity, a weak/falling financial-health score, or
collapsing margin. It is plain-English and conservative: with no statements to
check it reports ``checked=False`` (and stays "intact" — silence, not a false
all-clear). A leaf-ish consumer of the period series and the slice-34 quality math.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from traders.fundamental_periods import (
    ANNUAL_REPORTING_LAG_DAYS,
    QUARTERLY_REPORTING_LAG_DAYS,
    latest_periods,
)
from traders.quality import piotroski_score, quality_metrics

DEFAULT_MIN_PIOTROSKI = 5
DEFAULT_MIN_COMPUTABLE = 5
DEFAULT_MARGIN_DROP = 0.05  # >=5 percentage points of gross-margin erosion YoY


@dataclass(frozen=True)
class IntactReport:
    """Whether a holding's business premise still holds, with plain-English flags."""

    checked: bool  # did we have statements to judge against at all?
    intact: bool  # no broken-premise warnings fired
    warnings: list[str]


def thesis_intact(
    conn: sqlite3.Connection,
    ticker: str,
    *,
    as_of: str | None = None,
    min_piotroski: int = DEFAULT_MIN_PIOTROSKI,
    min_computable: int = DEFAULT_MIN_COMPUTABLE,
    margin_drop: float = DEFAULT_MARGIN_DROP,
    annual_lag_days: int = ANNUAL_REPORTING_LAG_DAYS,
    quarterly_lag_days: int = QUARTERLY_REPORTING_LAG_DAYS,
) -> IntactReport:
    """Flag business-level deterioration for ``ticker`` as of ``as_of`` (default today)."""
    when = as_of or date.today().isoformat()
    periods = latest_periods(
        conn,
        ticker,
        as_of=when,
        period_type="annual",
        limit=2,
        annual_lag_days=annual_lag_days,
        quarterly_lag_days=quarterly_lag_days,
    )
    if not periods:
        return IntactReport(checked=False, intact=True, warnings=[])
    cur = periods[0]
    prev = periods[1] if len(periods) > 1 else None
    warnings: list[str] = []

    if cur.net_income is not None and cur.net_income <= 0:
        warnings.append("Now lossmaking — net income is negative.")
    if cur.operating_cash_flow is not None and cur.capital_expenditure is not None:
        if cur.operating_cash_flow + cur.capital_expenditure <= 0:
            warnings.append(
                "Burning cash — owner earnings (operating cash flow minus capital "
                "spending) are negative."
            )
    if (
        cur.current_assets is not None
        and cur.current_liabilities is not None
        and cur.current_liabilities > 0
        and cur.current_assets < cur.current_liabilities
    ):
        warnings.append("Liquidity is tight — current assets are below current liabilities.")

    if prev is not None:
        score = piotroski_score(cur, prev)
        if score.computable >= min_computable and score.score < min_piotroski:
            warnings.append(f"Financial-health score is weak (Piotroski {score.score}/9).")
        metrics = quality_metrics(cur, prev)
        if metrics.gross_margin_delta is not None and metrics.gross_margin_delta <= -margin_drop:
            warnings.append(
                f"Gross margin is shrinking — down about "
                f"{abs(metrics.gross_margin_delta) * 100:.0f} points year-over-year."
            )

    return IntactReport(checked=True, intact=not warnings, warnings=warnings)
