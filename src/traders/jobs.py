"""Scheduled-job config + status — shared by the cron wrappers and the /jobs UI.

Two jobs (``daily``, ``weekly``) are wired to cron via ``scripts/*.sh``. This is
the small control surface they share with the web UI:

* an **enabled** flag per job, persisted in ``<data_dir>/jobs.json``. The cron
  runner scripts call ``traders jobs check NAME`` and skip when a job is off, so
  toggling it in the UI stops the work *without touching the crontab*.
* **last-run** status, parsed from ``<data_dir>/cron.log`` (the scripts' log), so
  the UI can show when each job last ran and whether it succeeded.

Pure stdlib, no DB. ``data_dir`` is where the SQLite db lives (``data`` by
default); callers pass it so jobs.json / cron.log sit beside the db and tests
stay hermetic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

JOBS: dict[str, dict[str, str]] = {
    "daily": {
        "label": "Daily run",
        "schedule": "0 8 * * 1-5",
        "description": "Refresh prices, then ranked Scout + signal theses (weekdays).",
    },
    "weekly": {
        "label": "Weekly review",
        "schedule": "0 9 * * 6",
        "description": "Reviewer post-mortems on closed positions (Saturday).",
    },
}

_DEFAULT_DATA_DIR = "data"
# Log headers the runner scripts emit, mapped to the job they belong to.
_HEADERS = {"daily run": "daily", "weekly review": "weekly"}


@dataclass(frozen=True)
class JobStatus:
    name: str
    label: str
    schedule: str
    description: str
    enabled: bool
    last_run: str | None
    last_status: str | None


def _config_path(data_dir: Path | str | None) -> Path:
    return Path(data_dir or _DEFAULT_DATA_DIR) / "jobs.json"


def _log_path(data_dir: Path | str | None) -> Path:
    return Path(data_dir or _DEFAULT_DATA_DIR) / "cron.log"


def load_config(data_dir: Path | str | None = None) -> dict[str, bool]:
    """Per-job enabled flags. Unknown/missing entries default to enabled."""
    path = _config_path(data_dir)
    raw: dict = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raw = {}
    return {name: bool(raw.get(name, {}).get("enabled", True)) for name in JOBS}


def is_enabled(name: str, data_dir: Path | str | None = None) -> bool:
    return load_config(data_dir).get(name, True)


def set_enabled(name: str, enabled: bool, data_dir: Path | str | None = None) -> None:
    """Persist a job's enabled flag. Raises ``ValueError`` for an unknown job."""
    if name not in JOBS:
        raise ValueError(f"unknown job {name!r} (expected one of {', '.join(JOBS)})")
    config = load_config(data_dir)
    config[name] = bool(enabled)
    path = _config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {n: {"enabled": config[n]} for n in JOBS}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def last_runs(data_dir: Path | str | None = None) -> dict[str, dict[str, str | None]]:
    """Most recent run timestamp + status per job, parsed from ``cron.log``.

    The runner scripts log ``===== YYYY-MM-DD HH:MM:SS TZ <phrase> =====`` then an
    ``[ok]`` / ``[error]`` / ``[warn]`` / ``[skip]`` marker; the fixed-width date
    after the leading ``=====`` makes this a robust scan.
    """
    out: dict[str, dict[str, str | None]] = {
        name: {"last_run": None, "last_status": None} for name in JOBS
    }
    path = _log_path(data_dir)
    if not path.is_file():
        return out
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    current: str | None = None
    for line in lines:
        if line.startswith("====="):
            current = None
            for phrase, name in _HEADERS.items():
                if phrase in line:
                    current = name
                    body = line.strip("= ").strip()
                    out[name]["last_run"] = body[:19] if len(body) >= 19 else None
                    out[name]["last_status"] = "running"
                    break
        elif current and line[:1] == "[" and "]" in line:
            out[current]["last_status"] = line[1 : line.index("]")]
    return out


def job_status(data_dir: Path | str | None = None) -> list[JobStatus]:
    """Per-job view for the UI / CLI: metadata + enabled flag + last run."""
    config = load_config(data_dir)
    runs = last_runs(data_dir)
    return [
        JobStatus(
            name=name,
            label=meta["label"],
            schedule=meta["schedule"],
            description=meta["description"],
            enabled=config[name],
            last_run=runs[name]["last_run"],
            last_status=runs[name]["last_status"],
        )
        for name, meta in JOBS.items()
    ]


def read_log_tail(data_dir: Path | str | None = None, lines: int = 40) -> str:
    """The last ``lines`` of ``cron.log`` (empty string if there is none)."""
    path = _log_path(data_dir)
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(content.splitlines()[-lines:])
