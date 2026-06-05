"""Tests for the user buy-list (slice 37 / B5) — hermetic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from traders.buylist import (
    BuyTarget,
    evaluate,
    get_target,
    load_targets,
    remove_target,
    set_target,
)
from traders.db import apply_migrations
from traders.prices import PriceHistory
from traders.valuation import IntrinsicValue

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def _history(**series) -> PriceHistory:
    return PriceHistory(series={t: tuple(rows) for t, rows in series.items()})


def _iv(buy_below: float) -> IntrinsicValue:
    return IntrinsicValue(
        owner_earnings=1.0,
        years=2,
        shares=1.0,
        price=100.0,
        iv_total=1.0,
        iv_per_share=buy_below / 0.8,
        margin_of_safety=0.2,
        buy_below_price=buy_below,
        implied_growth=0.0,
        discount_rate=0.10,
        terminal_growth=0.02,
    )


# ---- CRUD ------------------------------------------------------------------


def test_set_and_get_round_trip():
    conn = _conn()
    t = set_target(conn, "AAA", 100.0, note="cheap quality name")
    assert isinstance(t, BuyTarget)
    assert t.ticker == "AAA" and t.target_price == 100.0 and t.note == "cheap quality name"
    assert get_target(conn, "AAA") == t


def test_update_preserves_created_at_changes_target():
    conn = _conn()
    first = set_target(conn, "AAA", 100.0)
    second = set_target(conn, "AAA", 90.0, note="lower my price")
    assert second.created_at == first.created_at  # created_at preserved on update
    assert second.updated_at >= first.updated_at
    assert second.target_price == 90.0
    assert len(load_targets(conn)) == 1  # still one row


def test_remove_returns_whether_present():
    conn = _conn()
    set_target(conn, "AAA", 100.0)
    assert remove_target(conn, "AAA") is True
    assert remove_target(conn, "AAA") is False
    assert get_target(conn, "AAA") is None


def test_set_rejects_non_positive_target():
    conn = _conn()
    with pytest.raises(ValueError):
        set_target(conn, "AAA", 0.0)
    with pytest.raises(ValueError):
        set_target(conn, "AAA", -5.0)


def test_load_targets_sorted_by_ticker():
    conn = _conn()
    set_target(conn, "CCC", 30.0)
    set_target(conn, "AAA", 10.0)
    set_target(conn, "BBB", 20.0)
    assert [t.ticker for t in load_targets(conn)] == ["AAA", "BBB", "CCC"]


# ---- evaluation against latest prices --------------------------------------


def test_evaluate_triggers_when_price_at_or_below_target():
    conn = _conn()
    set_target(conn, "AAA", 100.0)
    history = _history(AAA=[("2026-01-01", 110.0), ("2026-02-01", 95.0)])  # latest 95 <= 100
    rows = evaluate(conn, history=history)
    assert len(rows) == 1
    r = rows[0]
    assert r.latest_price == 95.0 and r.latest_day == "2026-02-01"
    assert r.triggered is True
    assert r.distance_pct == pytest.approx((95.0 - 100.0) / 100.0 * 100.0)


def test_evaluate_not_triggered_when_price_above_target():
    conn = _conn()
    set_target(conn, "AAA", 90.0)
    rows = evaluate(conn, history=_history(AAA=[("2026-02-01", 99.0)]))
    assert rows[0].triggered is False
    assert rows[0].distance_pct == pytest.approx((99.0 - 90.0) / 90.0 * 100.0)


def test_evaluate_handles_missing_prices():
    conn = _conn()
    set_target(conn, "ZZZ", 100.0)
    rows = evaluate(conn, history=_history(AAA=[("2026-02-01", 50.0)]))
    assert rows[0].latest_price is None
    assert rows[0].triggered is False
    assert rows[0].distance_pct is None


def test_evaluate_surfaces_suggested_buy_below_from_valuation():
    conn = _conn()
    set_target(conn, "AAA", 100.0)
    rows = evaluate(
        conn,
        history=_history(AAA=[("2026-02-01", 95.0)]),
        valuation={"AAA": _iv(120.0)},
    )
    assert rows[0].suggested_buy_below == 120.0


def test_evaluate_suggested_is_none_without_valuation():
    conn = _conn()
    set_target(conn, "AAA", 100.0)
    rows = evaluate(conn, history=_history(AAA=[("2026-02-01", 95.0)]))
    assert rows[0].suggested_buy_below is None
