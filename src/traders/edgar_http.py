"""Shared SEC EDGAR HTTP plumbing (audit D3).

One home for the SEC-compliance surface: the ``TRADERS_EDGAR_UA`` gate, the
UA-stamped + size-capped request, and the ticker→CIK map. Three fetchers
(submissions, primary document, companyfacts) used to re-implement this
independently — three UA checks, three copies of the error string, three
timeouts — so a rate-limit/back-off or header fix had to land in all three or one
path would look anonymous to SEC and risk a block.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

UA_ENV = "TRADERS_EDGAR_UA"
_CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"


def require_ua() -> str:
    """Return the SEC User-Agent, or raise if ``TRADERS_EDGAR_UA`` is unset.

    SEC blocks anonymous traffic; it must be a real contact string such as
    'Acme Research user@acme.com'.
    """
    ua = os.environ.get(UA_ENV, "").strip()
    if not ua:
        raise RuntimeError(
            f"{UA_ENV} env var is required for the EDGAR data source. "
            "Set it to a real contact string (e.g. 'Acme Research user@acme.com')."
        )
    return ua


def get_bytes(url: str, ua: str, *, timeout: float = 15.0, max_bytes: int | None = None) -> bytes:
    """UA-stamped GET, body read under a size cap (audit H2)."""
    from traders.net import MAX_RESPONSE_BYTES, read_capped

    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (vetted SEC URLs)
        return read_capped(resp, MAX_RESPONSE_BYTES if max_bytes is None else max_bytes)


def get_json(url: str, ua: str, *, timeout: float = 15.0) -> Any:
    return json.loads(get_bytes(url, ua, timeout=timeout))


def load_cik_map(ua: str, *, timeout: float = 10.0) -> dict[str, str]:
    """ticker (upper-cased) -> zero-padded 10-digit CIK, from company_tickers.json."""
    payload = get_json(_CIK_MAP_URL, ua, timeout=timeout)
    out: dict[str, str] = {}
    entries = payload.values() if isinstance(payload, dict) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        if ticker and cik is not None:
            out[str(ticker).upper()] = str(cik).zfill(10)
    return out
