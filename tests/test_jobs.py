"""Tests for the scheduled-job config + status (slice 32)."""

from __future__ import annotations

import json

import pytest

from traders import jobs


def test_defaults_enabled_when_no_file(tmp_path):
    assert jobs.load_config(tmp_path) == {"daily": True, "weekly": True}
    assert jobs.is_enabled("daily", tmp_path) is True


def test_set_enabled_persists(tmp_path):
    jobs.set_enabled("daily", False, tmp_path)
    assert jobs.is_enabled("daily", tmp_path) is False
    assert jobs.is_enabled("weekly", tmp_path) is True  # untouched
    data = json.loads((tmp_path / "jobs.json").read_text())
    assert data["daily"]["enabled"] is False


def test_set_enabled_unknown_job_raises(tmp_path):
    with pytest.raises(ValueError):
        jobs.set_enabled("nope", True, tmp_path)


def test_last_runs_parsed_from_log(tmp_path):
    (tmp_path / "cron.log").write_text(
        "===== 2026-06-01 08:00:01 EEST daily run =====\n"
        "--- ingest-prices ---\n"
        "[ok] daily run complete\n\n"
        "===== 2026-06-07 09:00:02 EEST weekly review =====\n"
        "[error] run-weekly failed\n\n"
    )
    runs = jobs.last_runs(tmp_path)
    assert runs["daily"] == {"last_run": "2026-06-01 08:00:01", "last_status": "ok"}
    assert runs["weekly"] == {"last_run": "2026-06-07 09:00:02", "last_status": "error"}


def test_last_runs_uses_most_recent(tmp_path):
    (tmp_path / "cron.log").write_text(
        "===== 2026-06-01 08:00:01 EEST daily run =====\n[ok] daily run complete\n\n"
        "===== 2026-06-02 08:00:01 EEST daily run =====\n[skip] daily disabled via UI\n\n"
    )
    runs = jobs.last_runs(tmp_path)
    assert runs["daily"]["last_run"] == "2026-06-02 08:00:01"
    assert runs["daily"]["last_status"] == "skip"


def test_last_runs_empty_when_no_log(tmp_path):
    assert jobs.last_runs(tmp_path) == {
        "daily": {"last_run": None, "last_status": None},
        "weekly": {"last_run": None, "last_status": None},
    }


def test_job_status_combines_config_and_runs(tmp_path):
    jobs.set_enabled("weekly", False, tmp_path)
    statuses = {s.name: s for s in jobs.job_status(tmp_path)}
    assert statuses["daily"].enabled is True
    assert statuses["weekly"].enabled is False
    assert statuses["daily"].schedule == "0 8 * * 1-5"
    assert statuses["weekly"].label == "Weekly review"


def test_read_log_tail(tmp_path):
    (tmp_path / "cron.log").write_text("\n".join(f"line {i}" for i in range(100)))
    tail = jobs.read_log_tail(tmp_path, lines=10)
    assert tail.splitlines()[-1] == "line 99"
    assert len(tail.splitlines()) == 10
