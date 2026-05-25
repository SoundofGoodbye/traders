"""Data source abstractions for the Researcher.

The Researcher should never know whether data is real or stubbed. v1
ships the stub; a real adapter (yfinance today; FMP, Polygon, SEC
EDGAR later) drops in behind the same `DataSource` protocol without
touching the Researcher.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Protocol


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


_FUNDAMENTAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("trailingPE", "trailing P/E", "{:.2f}"),
    ("forwardPE", "forward P/E", "{:.2f}"),
    ("profitMargins", "profit margin", "{:.2%}"),
    ("revenueGrowth", "revenue growth", "{:.2%}"),
    ("marketCap", "market cap", "{:,.0f}"),
)


def _format_fundamentals(info: dict[str, Any]) -> str | None:
    """Pick a handful of stable fields and render a one-line snippet."""
    parts: list[str] = []
    for key, label, fmt in _FUNDAMENTAL_FIELDS:
        value = info.get(key)
        if value is None:
            continue
        try:
            parts.append(f"{label} {fmt.format(value)}")
        except (TypeError, ValueError):
            continue
    if not parts:
        return None
    return ", ".join(parts)


def _format_published_at(value: Any) -> str:
    """Coerce a yfinance published-at value into an ISO date string."""
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    if isinstance(value, str):
        # yfinance newer schemas: ISO 8601 with trailing Z. Take the date part.
        return value[:10]
    return date.today().isoformat()


def _extract_news_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the fields we care about out of either yfinance news shape.

    Older yfinance (pre-0.2.40) returns flat dicts with `title`, `link`,
    `providerPublishTime`. Newer versions wrap the payload under
    `content` with `clickThroughUrl`/`canonicalUrl` and `pubDate`.
    Return `None` if we can't find a title — that's the minimum we
    need to surface anything useful.
    """
    if not isinstance(item, dict):
        return None
    if "content" in item and isinstance(item["content"], dict):
        c = item["content"]
        title = c.get("title")
        url = None
        for key in ("canonicalUrl", "clickThroughUrl"):
            holder = c.get(key)
            if isinstance(holder, dict) and holder.get("url"):
                url = holder["url"]
                break
        if url is None and isinstance(c.get("clickThroughUrl"), str):
            url = c["clickThroughUrl"]
        published_at = _format_published_at(c.get("pubDate") or c.get("displayTime"))
        summary = c.get("summary") or c.get("description") or ""
    else:
        title = item.get("title")
        url = item.get("link")
        published_at = _format_published_at(item.get("providerPublishTime"))
        summary = item.get("summary") or item.get("publisher") or ""
    if not title:
        return None
    return {
        "title": title,
        "url": url or "",
        "published_at": published_at,
        "summary": summary,
    }


class YFinanceDataSource:
    """Real adapter backed by yfinance.

    Maps `Ticker.news` onto `kind="news"` DataPoints and selected
    `Ticker.info` fields onto a single `kind="fundamentals"`
    DataPoint. Network and parse errors return an empty list so a
    flaky ticker can't kill the Researcher run.

    yfinance does not serve filings, so this adapter never emits
    `kind="filing"` DataPoints — that gap is owned by a future EDGAR
    adapter.
    """

    def __init__(
        self,
        ticker_fn: Callable[[str], Any] | None = None,
        news_limit: int = 5,
    ) -> None:
        if ticker_fn is None:
            try:
                import yfinance
            except ImportError as e:
                raise ImportError(
                    "yfinance is not installed. Install with: uv sync --extra realdata"
                ) from e
            ticker_fn = yfinance.Ticker
        self._ticker_fn = ticker_fn
        self._news_limit = news_limit

    def fetch(self, ticker: str) -> list[DataPoint]:
        try:
            handle = self._ticker_fn(ticker)
        except Exception:
            return []
        points: list[DataPoint] = []
        points.extend(self._news_points(ticker, handle))
        fundamentals = self._fundamentals_point(ticker, handle)
        if fundamentals is not None:
            points.append(fundamentals)
        return points

    def _news_points(self, ticker: str, handle: Any) -> list[DataPoint]:
        try:
            raw = handle.news
        except Exception:
            return []
        if not raw:
            return []
        out: list[DataPoint] = []
        for item in raw[: self._news_limit]:
            extracted = _extract_news_item(item)
            if extracted is None:
                continue
            out.append(
                DataPoint(
                    kind="news",
                    title=extracted["title"],
                    url=extracted["url"],
                    snippet=extracted["summary"] or extracted["title"],
                    published_at=extracted["published_at"],
                )
            )
        return out

    def _fundamentals_point(self, ticker: str, handle: Any) -> DataPoint | None:
        try:
            info = handle.info
        except Exception:
            return None
        if not isinstance(info, dict) or not info:
            return None
        snippet = _format_fundamentals(info)
        if snippet is None:
            return None
        url = info.get("website") or f"https://finance.yahoo.com/quote/{ticker}"
        return DataPoint(
            kind="fundamentals",
            title=f"{ticker} key metrics",
            url=url,
            snippet=snippet,
            published_at=date.today().isoformat(),
        )


def make_data_source(name: str) -> DataSource:
    """Build a DataSource by name. Called from the CLI / orchestrator."""
    if name == "stub":
        return StubDataSource()
    if name == "yfinance":
        return YFinanceDataSource()
    raise ValueError(f"unknown data source: {name!r} (expected 'stub' or 'yfinance')")
