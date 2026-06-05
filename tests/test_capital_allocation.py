"""Tests for capital-allocation signals (slice 49 / B15) — hermetic."""

from __future__ import annotations

from traders.capital_allocation import capital_allocation_from_facts


def _flow(rows):
    return {"units": {"USD": rows}}


def _annual(end, val, filed):
    year = end[:4]
    return {"start": f"{year}-01-01", "end": end, "val": val, "form": "10-K", "filed": filed}


_FACTS = {
    "facts": {
        "us-gaap": {
            "PaymentsForRepurchaseOfCommonStock": _flow(
                [_annual("2024-12-31", 800, "2025-02-15"), _annual("2023-12-31", 600, "2024-02-15")]
            ),
            "PaymentsOfDividendsCommon": _flow(
                [_annual("2024-12-31", 200, "2025-02-15"), _annual("2023-12-31", 150, "2024-02-15")]
            ),
            "NetIncomeLoss": _flow(
                [
                    _annual("2024-12-31", 1000, "2025-02-15"),
                    _annual("2023-12-31", 900, "2024-02-15"),
                ]
            ),
            "CommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {"end": "2024-12-31", "val": 900, "form": "10-K", "filed": "2025-02-15"},
                        {"end": "2023-12-31", "val": 1000, "form": "10-K", "filed": "2024-02-15"},
                    ]
                }
            },
        }
    }
}


def test_capital_allocation_summarizes_returns_and_share_count():
    ca = capital_allocation_from_facts(_FACTS)
    assert ca is not None
    assert ca.years == 2
    assert ca.total_buybacks == 1400  # 800 + 600
    assert ca.total_dividends == 350  # 200 + 150
    assert ca.total_returned == 1750
    assert ca.total_net_income == 1900
    assert abs(ca.payout_ratio - 1750 / 1900) < 1e-9
    # shares fell 1000 -> 900 = -10%
    assert abs(ca.share_change_pct - (-10.0)) < 1e-9


def test_capital_allocation_flags_buybacks_shrinking_share_count():
    ca = capital_allocation_from_facts(_FACTS)
    assert any("shrinking" in n for n in ca.notes)
    assert any("Returns cash to shareholders" in n for n in ca.notes)


def test_capital_allocation_flags_dilution():
    facts = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": _flow([_annual("2024-12-31", 100, "2025-02-15")]),
                "CommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "end": "2023-12-31",
                                "val": 1000,
                                "form": "10-K",
                                "filed": "2024-02-15",
                            },
                            {
                                "end": "2024-12-31",
                                "val": 1100,
                                "form": "10-K",
                                "filed": "2025-02-15",
                            },
                        ]
                    }
                },
            }
        }
    }
    ca = capital_allocation_from_facts(facts)
    assert any("dilution" in n.lower() for n in ca.notes)


def test_capital_allocation_none_without_data():
    assert capital_allocation_from_facts({}) is None
    assert capital_allocation_from_facts({"facts": {"us-gaap": {}}}) is None
