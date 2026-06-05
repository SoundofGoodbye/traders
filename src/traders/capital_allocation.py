"""Capital-allocation signals from SEC EDGAR companyfacts.

Backlog item B15 — how management deploys cash is core to owner-mindset judgment,
and it's right there in the official filings: buybacks
(``PaymentsForRepurchaseOfCommonStock``), dividends (``PaymentsOfDividends*``), net
income, and the share count. ``capital_allocation_from_facts`` summarizes the
recent record — total cash returned, payout ratio, and whether the share count is
*shrinking* (buybacks compounding per-share value) or *growing* (dilution) — in
plain English.

Pure over the same companyfacts payload slice 47 already fetches (reuses its
annual-10-K folding), so it's hermetic. Insider buying (Form 4) is a separate,
heavier XML feed and stays deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from traders.edgar_fundamentals import _merge_annual

DEFAULT_MAX_YEARS = 5


@dataclass(frozen=True)
class CapitalAllocation:
    """A company's recent capital-allocation record, with plain-English flags."""

    years: int
    total_buybacks: float
    total_dividends: float
    total_returned: float
    total_net_income: float
    payout_ratio: float | None  # cash returned / net income over the window
    share_change_pct: float | None  # share-count change across the window (− = buybacks)
    notes: list[str]


def _annual_series(facts: dict, concepts: tuple[str, ...], *, duration: bool) -> dict[str, float]:
    """``end_date -> value`` for a concept's annual 10-K entries (value only)."""
    merged: dict[str, tuple[float, str | None]] = {}
    ns_facts = facts.get("us-gaap", {})
    for concept in concepts:
        if concept in ns_facts:
            _merge_annual(merged, ns_facts[concept], duration)
    return {end: val for end, (val, _) in merged.items()}


def capital_allocation_from_facts(
    companyfacts: dict[str, Any], *, max_years: int = DEFAULT_MAX_YEARS
) -> CapitalAllocation | None:
    """Summarize the recent capital-allocation record, or None without the data."""
    facts = companyfacts.get("facts", {}) if isinstance(companyfacts, dict) else {}
    buybacks = _annual_series(facts, ("PaymentsForRepurchaseOfCommonStock",), duration=True)
    dividends = _annual_series(
        facts, ("PaymentsOfDividendsCommon", "PaymentsOfDividends"), duration=True
    )
    net_income = _annual_series(facts, ("NetIncomeLoss",), duration=True)
    shares = _annual_series(facts, ("CommonStockSharesOutstanding",), duration=False)

    ends = sorted(set(buybacks) | set(dividends) | set(net_income), reverse=True)[:max_years]
    if not ends:
        return None
    total_buybacks = sum(buybacks.get(e, 0.0) for e in ends)
    total_dividends = sum(dividends.get(e, 0.0) for e in ends)
    total_returned = total_buybacks + total_dividends
    total_net_income = sum(net_income.get(e, 0.0) for e in ends)
    payout = total_returned / total_net_income if total_net_income > 0 else None

    share_change = None
    share_ends = sorted(shares)
    if len(share_ends) >= 2 and shares[share_ends[0]]:
        share_change = (
            (shares[share_ends[-1]] - shares[share_ends[0]]) / shares[share_ends[0]] * 100
        )

    notes: list[str] = []
    if total_returned > 0:
        notes.append("Returns cash to shareholders (buybacks + dividends).")
    if share_change is not None and share_change <= -2:
        notes.append(
            f"Share count is shrinking ({share_change:.0f}% over the window) — buybacks "
            "are compounding per-share value."
        )
    elif share_change is not None and share_change >= 2:
        notes.append(f"Share count is growing ({share_change:+.0f}%) — watch for dilution.")
    if payout is not None and payout > 1.0:
        notes.append("Returning more cash than it earns — check that's sustainable.")

    return CapitalAllocation(
        years=len(ends),
        total_buybacks=total_buybacks,
        total_dividends=total_dividends,
        total_returned=total_returned,
        total_net_income=total_net_income,
        payout_ratio=payout,
        share_change_pct=share_change,
        notes=notes,
    )
