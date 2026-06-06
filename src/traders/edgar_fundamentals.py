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


def companyfacts_fetcher() -> Callable[[str], dict[str, Any]]:
    """Build the raw EDGAR companyfacts fetcher (ticker -> companyfacts dict).

    Network only when called; hard-requires ``TRADERS_EDGAR_UA`` (SEC blocks
    anonymous traffic). The ticker→CIK map is fetched once and cached in closure.
    Returns ``{}`` for an unknown ticker or any fetch error. Shared by the periods
    ingestor and the capital-allocation analysis (slice 49).
    """
    from traders import edgar_http

    ua = edgar_http.require_ua()
    cik_map: dict[str, str] = {}

    def fetch(ticker: str) -> dict[str, Any]:
        if not cik_map:
            cik_map.update(edgar_http.load_cik_map(ua))
        cik = cik_map.get(ticker.upper())
        if cik is None:
            return {}
        try:
            return edgar_http.get_json(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", ua, timeout=15
            )
        except Exception:
            return {}

    return fetch


def _default_edgar_facts_fetcher() -> Callable[[str], list[dict[str, Any]]]:
    """The periods fetcher: raw companyfacts mapped to normalized period dicts."""
    raw = companyfacts_fetcher()
    return lambda ticker: companyfacts_to_periods(raw(ticker))
