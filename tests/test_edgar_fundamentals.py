"""Tests for the EDGAR companyfacts -> periods mapper (slice 47 / B13) — hermetic."""

from __future__ import annotations

from traders.edgar_fundamentals import companyfacts_to_periods
from traders.fundamental_periods import periods_from_statements


def _usd(rows):
    return {"units": {"USD": rows}}


_FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": _usd(
                [
                    {
                        "start": "2023-01-01",
                        "end": "2023-12-31",
                        "val": 1000,
                        "form": "10-K",
                        "filed": "2024-02-15",
                    },
                    {
                        "start": "2022-01-01",
                        "end": "2022-12-31",
                        "val": 900,
                        "form": "10-K",
                        "filed": "2023-02-15",
                    },
                    # quarterly entry with a non-annual duration -> must be ignored
                    {
                        "start": "2023-07-01",
                        "end": "2023-09-30",
                        "val": 250,
                        "form": "10-Q",
                        "filed": "2023-10-15",
                    },
                ]
            ),
            "NetIncomeLoss": _usd(
                [
                    {
                        "start": "2023-01-01",
                        "end": "2023-12-31",
                        "val": 120,
                        "form": "10-K",
                        "filed": "2024-02-15",
                    },
                    {
                        "start": "2022-01-01",
                        "end": "2022-12-31",
                        "val": 100,
                        "form": "10-K",
                        "filed": "2023-02-15",
                    },
                ]
            ),
            "Assets": _usd(
                [
                    {"end": "2023-12-31", "val": 2100, "form": "10-K", "filed": "2024-02-15"},
                    {"end": "2022-12-31", "val": 2000, "form": "10-K", "filed": "2023-02-15"},
                ]
            ),
            "PaymentsToAcquirePropertyPlantAndEquipment": _usd(
                [
                    {
                        "start": "2023-01-01",
                        "end": "2023-12-31",
                        "val": 50,
                        "form": "10-K",
                        "filed": "2024-02-15",
                    }
                ]
            ),
            "CommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {"end": "2023-12-31", "val": 1000, "form": "10-K", "filed": "2024-02-15"}
                    ]
                }
            },
        }
    }
}


def test_companyfacts_maps_annual_periods():
    periods = companyfacts_to_periods(_FACTS)
    ends = [p["period_end"] for p in periods]
    assert ends == ["2023-12-31", "2022-12-31"]  # newest first, quarterly ignored
    p23 = periods[0]
    assert p23["period_type"] == "annual"
    assert p23["revenue"] == 1000
    assert p23["net_income"] == 120
    assert p23["total_assets"] == 2100
    assert p23["capital_expenditure"] == -50  # stored negative (outflow)
    assert p23["shares_outstanding"] == 1000
    assert p23["available_at"] == "2024-02-15"  # the real filing date


def test_quarterly_entries_are_excluded():
    periods = companyfacts_to_periods(_FACTS)
    assert "2023-09-30" not in [p["period_end"] for p in periods]


def test_maps_through_periods_from_statements():
    # The mapper output drops straight into the slice-33 ingestion path.
    rows = periods_from_statements("AAPL", companyfacts_to_periods(_FACTS), source="edgar")
    assert len(rows) == 2
    cur = rows[0]
    assert cur.ticker == "AAPL" and cur.source == "edgar"
    assert cur.period_end == "2023-12-31"
    assert cur.capital_expenditure == -50.0
    assert cur.available_at == "2024-02-15"


def test_empty_facts_yields_no_periods():
    assert companyfacts_to_periods({}) == []
    assert companyfacts_to_periods({"facts": {}}) == []
