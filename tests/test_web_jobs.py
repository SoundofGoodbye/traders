import re

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from traders import jobs  # noqa: E402
from traders.web.app import create_app  # noqa: E402


def _csrf_token(client):
    """Prime the CSRF cookie via a GET on /jobs and return the raw token."""
    resp = client.get("/jobs")
    match = re.search(r"csrftoken=([^;]+)", resp.headers.get("set-cookie", ""))
    cookie_value = match.group(1) if match else client.cookies.get("csrftoken")
    if cookie_value:
        client.cookies.set("csrftoken", cookie_value)
    return cookie_value.split(".")[0] if cookie_value else None


def test_jobs_page_lists_jobs(tmp_path):
    client = TestClient(create_app(tmp_path / "t.db"))
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert "Daily run" in resp.text
    assert "Weekly review" in resp.text
    assert "0 8 * * 1-5" in resp.text  # the raw schedule is still shown for reference
    # ...alongside its plain-English cadence
    assert "Every weekday at 08:00" in resp.text
    assert "Every Saturday at 09:00" in resp.text


def test_toggle_disables_then_enables(tmp_path):
    client = TestClient(create_app(tmp_path / "t.db"))
    token = _csrf_token(client)
    assert jobs.is_enabled("daily", tmp_path) is True  # default

    resp = client.post("/jobs/daily/toggle", data={"csrf_token": token})
    assert resp.status_code == 200  # followed 303 -> GET /jobs
    assert jobs.is_enabled("daily", tmp_path) is False
    assert jobs.is_enabled("weekly", tmp_path) is True  # only daily flipped

    client.post("/jobs/daily/toggle", data={"csrf_token": token})
    assert jobs.is_enabled("daily", tmp_path) is True  # toggling back on


def test_toggle_csrf_rejected(tmp_path):
    client = TestClient(create_app(tmp_path / "t.db"))  # no GET -> no cookie
    resp = client.post("/jobs/daily/toggle", data={})
    assert resp.status_code == 403
    assert jobs.is_enabled("daily", tmp_path) is True  # unchanged


def test_toggle_unknown_job_is_404(tmp_path):
    client = TestClient(create_app(tmp_path / "t.db"))
    token = _csrf_token(client)
    resp = client.post("/jobs/bogus/toggle", data={"csrf_token": token})
    assert resp.status_code == 404
