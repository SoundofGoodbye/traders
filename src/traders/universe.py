"""Universe coverage — which watchlist names are actually priceable.

Backlog item B11: the watchlist advertises S&P 100 + EuroStoxx 50, but the free
price tiers are US end-of-day only, so every foreign-venue ticker (``.PA``,
``.DE``, …) is *silently* skipped at ingest — the advertised universe and the real
one diverge. This makes the gap honest: ``classify`` splits a ticker list into
priceable (the price source's symbol map resolves it) and skipped (it maps to
``None``), so ``traders universe`` and the docs can state what's actually covered.

It reads only the symbol maps (no network, no API key) — building a price *source*
would require a token, but classifying coverage must not.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from traders.scout import load_watchlist
from traders.stooq_symbols import to_stooq_symbol
from traders.tiingo import to_tiingo_symbol

_SYMBOL_MAPS: dict[str, Callable[[str], str | None]] = {
    "tiingo": to_tiingo_symbol,
    "stooq": to_stooq_symbol,
}


@dataclass(frozen=True)
class UniverseReport:
    """Coverage of a watchlist under a given price source."""

    source: str
    total: int
    priceable: list[str]
    skipped: list[str]  # mapped to None — not on the source's free tier


def classify(tickers: list[str], source: str = "tiingo") -> UniverseReport:
    """Split ``tickers`` into priceable vs skipped under ``source``'s symbol map."""
    symbol_map = _SYMBOL_MAPS.get(source)
    if symbol_map is None:
        raise ValueError(f"unknown price source: {source!r} (expected 'tiingo' or 'stooq')")
    priceable: list[str] = []
    skipped: list[str] = []
    for ticker in tickers:
        (priceable if symbol_map(ticker) else skipped).append(ticker)
    return UniverseReport(source=source, total=len(tickers), priceable=priceable, skipped=skipped)


def classify_watchlist(path: Path | None = None, source: str = "tiingo") -> UniverseReport:
    """Classify the watchlist JSON at ``path`` (default packaged watchlist)."""
    return classify(load_watchlist(path), source)
