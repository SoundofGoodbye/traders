"""Data source abstractions for the Researcher.

The Researcher should never know whether data is real or stubbed. v1
ships the stub; a real adapter (yfinance, FMP, SEC EDGAR, etc.) drops
in behind the same `DataSource` protocol in slice 7+ without touching
the Researcher.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DataPoint:
    """One piece of evidence about a ticker, with a citable source."""

    kind: str
    title: str
    url: str
    snippet: str
    published_at: str


class DataSource(Protocol):
    """Fetch evidence for a single ticker.

    Implementations return an empty list rather than raising when a
    ticker has no data, so the Researcher can still record a (sparse)
    note instead of skipping the candidate entirely.
    """

    def fetch(self, ticker: str) -> list[DataPoint]: ...


class StubDataSource:
    """Canned fixture data — deterministic per ticker, no I/O.

    Mirrors the shape a real adapter will return so downstream code is
    indifferent to the swap.
    """

    def fetch(self, ticker: str) -> list[DataPoint]:
        return [
            DataPoint(
                kind="filing",
                title=f"{ticker} latest 10-Q",
                url=f"stub://filings/{ticker}/10q",
                snippet=f"[stub] Most recent quarterly filing for {ticker}.",
                published_at="2026-05-15",
            ),
            DataPoint(
                kind="news",
                title=f"{ticker} headline coverage",
                url=f"stub://news/{ticker}/headline",
                snippet=f"[stub] Recent news mentions for {ticker}.",
                published_at="2026-05-18",
            ),
            DataPoint(
                kind="fundamentals",
                title=f"{ticker} key metrics",
                url=f"stub://fundamentals/{ticker}",
                snippet=f"[stub] P/E, revenue growth, margins for {ticker}.",
                published_at="2026-05-20",
            ),
        ]
