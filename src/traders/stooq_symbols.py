"""Map watchlist tickers (Yahoo-style) to Stooq symbols.

Centralizes the #1 ingestion footgun: the watchlist uses Yahoo conventions
(``AAPL``, ``BRK.B``, ``MC.PA``, ``ADS.DE``) but Stooq uses lowercase
``symbol.market`` codes (``aapl.us``, ``brk-b.us``, ``mc.fr``, ``ads.de``).

Pure function, no I/O. Unknown/unsupported suffixes return ``None`` so the
ingestor skips them rather than fetching garbage — Stooq's free EU coverage is
partial and best-effort; the US set is reliable.
"""

from __future__ import annotations

# Yahoo exchange suffix -> Stooq market code. Best-effort for EU venues.
_EU_SUFFIX = {
    "DE": "de",  # Deutsche Börse / Xetra
    "PA": "fr",  # Euronext Paris
    "AS": "nl",  # Euronext Amsterdam
    "MI": "it",  # Borsa Italiana (Milan)
    "MC": "es",  # Bolsa de Madrid
    "IR": "ie",  # Euronext Dublin
}


def to_stooq_symbol(ticker: str) -> str | None:
    """Convert a watchlist ticker to a Stooq symbol, or ``None`` if unsupported.

    - ``AAPL`` -> ``aapl.us``
    - ``BRK.B`` -> ``brk-b.us`` (US class share: dot becomes a dash)
    - ``MC.PA`` -> ``mc.fr``, ``ADS.DE`` -> ``ads.de`` (known EU venues)
    - anything with an unrecognized suffix -> ``None``
    """
    t = ticker.strip().upper()
    if not t:
        return None
    if "." not in t:
        return f"{t.lower()}.us"
    base, _, suffix = t.rpartition(".")
    if not base:
        return None
    if suffix in _EU_SUFFIX:
        return f"{base.lower()}.{_EU_SUFFIX[suffix]}"
    # A single trailing letter is a US class share (BRK.B, BF.B).
    if len(suffix) == 1 and suffix.isalpha():
        return f"{base.lower()}-{suffix.lower()}.us"
    return None
