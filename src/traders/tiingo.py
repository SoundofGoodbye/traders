"""Tiingo end-of-day price source — adjusted closes for the ``prices`` table.

Stooq gated its free CSV endpoint (it now demands an apikey), so Tiingo is the
default real price source from slice 30. Tiingo serves split/dividend-**adjusted**
end-of-day data as CSV — the right input for return-based signals — with 30+
years of history on the free tier (~50 symbols/hour, ample for the watchlist).

Same shape as the Stooq helpers, so it drops into the existing ingestor: a pure
``to_tiingo_symbol`` map, a ``parse_tiingo_csv`` reader keyed on ``adjClose``, and
a ``_default_tiingo_fetcher`` that hides the network behind an injected closure.
The fetcher hard-requires the ``TIINGO_API_KEY`` env var (free key at tiingo.com)
and never logs it — mirroring the EDGAR adapter's ``TRADERS_EDGAR_UA`` rule.
Tiingo's free tier is US EOD, so foreign-venue suffixes (``.PA``, ``.DE``, …) map
to ``None`` (skipped) rather than wasting rate-limited calls.
"""

from __future__ import annotations

import csv
import io
from typing import Callable

_EOD_URL = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"


def to_tiingo_symbol(ticker: str) -> str | None:
    """Watchlist ticker -> Tiingo symbol, or ``None`` if unsupported on the free tier.

    - ``AAPL`` -> ``AAPL``
    - ``BRK.B`` -> ``BRK-B`` (US class share: dot becomes a dash)
    - foreign-venue suffixes (``MC.PA``, ``ADS.DE``) -> ``None`` (Tiingo free = US EOD)
    """
    t = ticker.strip().upper()
    if not t:
        return None
    if "." not in t:
        return t
    base, _, suffix = t.rpartition(".")
    # A single trailing letter is a US class share (BRK.B, BF.B).
    if base and len(suffix) == 1 and suffix.isalpha():
        return f"{base}-{suffix}"
    return None


def parse_tiingo_csv(text: str) -> list[tuple[str, float]]:
    """Parse Tiingo daily CSV into ``[(iso_day, adjClose)]``, dropping bad rows.

    Uses the split/dividend-adjusted close (``adjClose``) and skips rows without a
    parseable one. Dates are truncated to ``YYYY-MM-DD`` (Tiingo CSV gives a plain
    date, but a trailing timestamp is tolerated).
    """
    out: list[tuple[str, float]] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        day = (row.get("date") or "").strip()[:10]
        raw_close = (row.get("adjClose") or "").strip()
        if not day or not raw_close:
            continue
        try:
            out.append((day, float(raw_close)))
        except ValueError:
            continue
    return out


def _default_tiingo_fetcher(
    token: str | None = None, start_date: str | None = None
) -> Callable[[str], str]:
    """Build the real Tiingo fetcher (stdlib urllib). Network only when called.

    Hard-requires ``TIINGO_API_KEY`` (or an explicit ``token``); raises
    ``RuntimeError`` at construction otherwise — fail fast at the boundary, like
    the EDGAR adapter. An optional ``start_date`` (ISO) limits fetched history.
    """
    import os
    import urllib.parse
    import urllib.request

    from traders.net import read_capped

    tok = (token or os.environ.get("TIINGO_API_KEY", "")).strip()
    if not tok:
        raise RuntimeError(
            "TIINGO_API_KEY env var is required for the Tiingo price source. "
            "Get a free key at https://www.tiingo.com and `export TIINGO_API_KEY=...`."
        )

    def fetch(symbol: str) -> str:
        # Token rides in the Authorization header, not the query string (audit L2):
        # query params land in proxy/server access logs; headers do not.
        params: dict[str, str] = {"format": "csv"}
        if start_date:
            params["startDate"] = start_date
        base = _EOD_URL.format(symbol=urllib.parse.quote(symbol))
        url = f"{base}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "traders-paper/1.0", "Authorization": f"Token {tok}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return read_capped(resp).decode("utf-8", errors="replace")

    return fetch
