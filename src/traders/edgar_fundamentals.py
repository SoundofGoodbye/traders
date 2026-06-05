"""SEC EDGAR companyfacts → period-by-period fundamentals (official, audited, free).

Backlog item B13 — "a fundamentals provider you'd stake money on". yfinance is
scraped and fragile; SEC EDGAR's ``companyfacts`` XBRL API is the official, audited
source, free, and carries the *real filing date* per fact (so look-ahead safety is
exact, not a reporting-lag estimate). This maps companyfacts JSON onto the same
normalized per-period dicts ``fundamental_periods.periods_from_statements`` consumes,
so it drops into ``ingest-fundamental-periods --source edgar`` with no schema change.

The mapper (``companyfacts_to_periods``) is pure and hermetic — tested against
canned companyfacts. Only ``_default_edgar_facts_fetcher`` touches the network
(ticker→CIK via ``company_tickers.json``, then the per-CIK companyfacts), and it
hard-requires ``TRADERS_EDGAR_UA`` like the slice-10 EDGAR data source.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import date
from typing import Any, Callable

# field -> candidate us-gaap/dei XBRL concept tags (first present wins).
_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "gross_profit": ("GrossProfit",),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
    "total_assets": ("Assets",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "total_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "shares_outstanding": (
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ),
}

# Flow ("duration") items span a period; balance items are point-in-time. We only
# accept annual durations (>= ~300 days) so quarterly entries with the same end
# date don't masquerade as the fiscal year.
_DURATION_FIELDS = frozenset(
    {"revenue", "gross_profit", "net_income", "operating_cash_flow", "capital_expenditure"}
)
_ANNUAL_MIN_DAYS = 300
_ANCHOR_FIELDS = ("net_income", "total_assets", "revenue")


def _merge_annual(
    dest: dict[str, tuple[float, str | None]], concept_obj: dict, duration: bool
) -> None:
    """Fold a concept's annual 10-K entries into ``end_date -> (value, filed)``."""
    units = concept_obj.get("units", {})
    entries = units.get("USD") or units.get("shares") or []
    for e in entries:
        if not str(e.get("form", "")).startswith("10-K"):
            continue
        end, val, filed = e.get("end"), e.get("val"), e.get("filed")
        if end is None or not isinstance(val, (int, float)):
            continue
        if duration:
            start = e.get("start")
            try:
                if (
                    start is None
                    or (date.fromisoformat(end) - date.fromisoformat(start)).days < _ANNUAL_MIN_DAYS
                ):
                    continue
            except (ValueError, TypeError):
                continue
        prev = dest.get(end)
        # Keep the latest-filed entry per period end (amendments supersede).
        if prev is None or (filed and (prev[1] is None or filed >= prev[1])):
            dest[end] = (float(val), filed)


def companyfacts_to_periods(companyfacts: dict[str, Any]) -> list[dict[str, Any]]:
    """Map an EDGAR companyfacts payload onto normalized annual period dicts."""
    facts = companyfacts.get("facts", {}) if isinstance(companyfacts, dict) else {}
    field_maps: dict[str, dict[str, tuple[float, str | None]]] = {}
    for field, concepts in _CONCEPTS.items():
        merged: dict[str, tuple[float, str | None]] = {}
        for namespace in ("us-gaap", "dei"):
            ns_facts = facts.get(namespace, {})
            for concept in concepts:
                if concept in ns_facts:
                    _merge_annual(merged, ns_facts[concept], field in _DURATION_FIELDS)
        field_maps[field] = merged

    ends: set[str] = set()
    for anchor in _ANCHOR_FIELDS:
        ends |= set(field_maps[anchor])

    periods: list[dict[str, Any]] = []
    for end in sorted(ends, reverse=True):
        period: dict[str, Any] = {"period_end": end, "period_type": "annual"}
        filed: str | None = None
        for field, values in field_maps.items():
            if end not in values:
                continue
            val, entry_filed = values[end]
            # XBRL reports capex as a positive outflow; store it negative to match
            # the convention (owner earnings = operating cash flow + capex).
            period[field] = -abs(val) if field == "capital_expenditure" else val
            filed = filed or entry_filed
        if filed:
            period["available_at"] = filed
        periods.append(period)
    return periods


def _default_edgar_facts_fetcher() -> Callable[[str], list[dict[str, Any]]]:
    """Build the real EDGAR companyfacts fetcher (ticker -> normalized periods).

    Network only when called; hard-requires ``TRADERS_EDGAR_UA`` (SEC blocks
    anonymous traffic). The ticker→CIK map is fetched once and cached in closure.
    """
    ua = os.environ.get("TRADERS_EDGAR_UA", "").strip()
    if not ua:
        raise RuntimeError(
            "TRADERS_EDGAR_UA env var is required for the EDGAR fundamentals source. "
            "Set it to a contact string like 'Your Name you@example.com'."
        )
    cik_map: dict[str, str] = {}

    def _get_json(url: str) -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (vetted SEC URLs)
            return json.loads(resp.read().decode())

    def fetch(ticker: str) -> list[dict[str, Any]]:
        if not cik_map:
            payload = _get_json("https://www.sec.gov/files/company_tickers.json")
            for entry in payload.values():
                cik, sym = entry.get("cik_str"), entry.get("ticker")
                if cik is not None and sym:
                    cik_map[str(sym).upper()] = str(cik).zfill(10)
        cik = cik_map.get(ticker.upper())
        if cik is None:
            return []
        try:
            facts = _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        except Exception:
            return []
        return companyfacts_to_periods(facts)

    return fetch
