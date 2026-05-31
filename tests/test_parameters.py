"""Tests for learned-parameter loading, saving, and agent wiring."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from traders.db import apply_migrations
from traders.parameters import (
    LearnedParameters,
    load_parameters,
    parameter_names,
    save_parameters,
)
from traders.portfolio import run as pm_run
from traders.scout import run as scout_run

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn, MIGRATIONS)
    return conn


def test_packaged_defaults_match_original_constants():
    params = load_parameters()
    assert params.batch_size == 10
    assert params.max_total_size_pct == 20.0


def test_parameter_names_lists_the_knobs():
    assert parameter_names() == ("batch_size", "max_total_size_pct")


def test_load_explicit_path(tmp_path: Path):
    p = tmp_path / "learned_parameters.json"
    p.write_text(json.dumps({"batch_size": 3, "max_total_size_pct": 12.5}))

    params = load_parameters(p)

    assert params.batch_size == 3
    assert params.max_total_size_pct == 12.5


def test_save_then_load_round_trip(tmp_path: Path):
    p = tmp_path / "learned_parameters.json"
    saved = LearnedParameters(batch_size=7, max_total_size_pct=33.0)

    save_parameters(saved, p)

    assert load_parameters(p) == saved


def test_partial_override_keeps_other_defaults(tmp_path: Path):
    p = tmp_path / "learned_parameters.json"
    p.write_text(json.dumps({"batch_size": 4}))

    params = load_parameters(p)

    assert params.batch_size == 4
    assert params.max_total_size_pct == 20.0  # unspecified -> default


def test_scout_uses_params_batch_size_when_not_overridden(tmp_path: Path):
    conn = _conn()
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["A", "B", "C", "D", "E"], "eurostoxx50": []}))

    _, picks = scout_run(
        conn,
        watchlist_path=wl,
        run_date=date(2025, 1, 1),
        params=LearnedParameters(batch_size=2),
    )

    assert len(picks) == 2


def test_explicit_batch_size_overrides_params(tmp_path: Path):
    conn = _conn()
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"sp100": ["A", "B", "C", "D", "E"], "eurostoxx50": []}))

    _, picks = scout_run(
        conn,
        watchlist_path=wl,
        run_date=date(2025, 1, 1),
        batch_size=4,
        params=LearnedParameters(batch_size=2),
    )

    assert len(picks) == 4


def test_pm_uses_params_exposure_cap(tmp_path: Path):
    conn = _conn()
    # Two theses; a zero exposure cap must reject both.
    for ticker in ("AAA", "BBB"):
        conn.execute(
            "INSERT INTO theses (ticker, thesis_type, direction, conviction,"
            " suggested_size_pct, created_at, status, run_id)"
            " VALUES (?, 'momentum', 'long', 3, 5.0,"
            " '2025-01-01T00:00:00+00:00', 'open', 1)",
            (ticker,),
        )
    conn.commit()

    report = pm_run(conn, analyst_run_id=1, params=LearnedParameters(max_total_size_pct=0.0))

    assert report.accepted == []
    assert len(report.rejected) == 2
