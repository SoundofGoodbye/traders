"""Minimal .env loader — zero-dependency, opt-in local config (slice 31).

Reads ``KEY=VALUE`` lines from a ``.env`` file into ``os.environ`` so secrets
like ``TIINGO_API_KEY`` and ``TRADERS_EDGAR_UA`` live in one gitignored file
instead of being exported by hand each shell. No dependency on ``python-dotenv``
— the core install stays empty.

Deliberately conservative: it **never overwrites** a variable already set in the
real environment (an explicit ``export`` always wins, and the test suite stays
hermetic), a missing file is a silent no-op, and it skips blank lines, ``#``
comments, a leading ``export``, and lines without ``=``. The CLI calls
``load_dotenv()`` once at startup against ``./.env`` (run from the repo root).
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | str = ".env") -> dict[str, str]:
    """Load ``KEY=VALUE`` pairs from ``path`` into ``os.environ`` (set-if-absent).

    Returns the names actually set (i.e. those not already in the environment).
    A missing file returns ``{}``.
    """
    p = Path(path)
    if not p.is_file():
        return {}
    loaded: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key or key in os.environ:  # never clobber the real environment
            continue
        os.environ[key] = value
        loaded[key] = value
    return loaded
