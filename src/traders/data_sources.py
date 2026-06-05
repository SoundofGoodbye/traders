"""Data source abstractions for the Researcher.

The Researcher should never know whether data is real or stubbed. v1
ships the stub; a real adapter (yfinance today; FMP, Polygon, SEC
EDGAR later) drops in behind the same `DataSource` protocol without
touching the Researcher.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


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
        except Exception as e:
            logger.warning("yfinance lookup failed for %s: %s", ticker, e)
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
        except Exception as e:
            logger.warning("news fetch failed for %s: %s", ticker, e)
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


def _default_edgar_fetcher() -> Callable[[str], list[dict[str, Any]]]:
    """Build the default EDGAR fetcher.

    Hides the two-step SEC EDGAR shape (ticker -> CIK map, then per-CIK
    submissions JSON) behind one `filings_fn(ticker) -> list[dict]`.
    The CIK map is cached in closure state after the first call.

    Hard-requires the `TRADERS_EDGAR_UA` env var: SEC blocks requests
    without an identifying User-Agent (a real contact string like
    "Acme Research user@acme.com"). Raises RuntimeError at construction
    if unset - same shape as how yfinance raises when the package
    isn't installed.
    """
    import json
    import os
    import urllib.request

    ua = os.environ.get("TRADERS_EDGAR_UA", "").strip()
    if not ua:
        raise RuntimeError(
            "TRADERS_EDGAR_UA env var is required for the EDGAR data source. "
            "Set it to a real contact string (e.g. "
            "'Acme Research user@acme.com')."
        )

    cik_map: dict[str, str] = {}

    def _fetch_json(url: str) -> Any:
        from traders.net import read_capped

        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(read_capped(resp))

    def _load_cik_map() -> dict[str, str]:
        payload = _fetch_json("https://www.sec.gov/files/company_tickers.json")
        out: dict[str, str] = {}
        for entry in payload.values():
            if not isinstance(entry, dict):
                continue
            t = entry.get("ticker")
            c = entry.get("cik_str")
            if t and c is not None:
                out[str(t).upper()] = str(c).zfill(10)
        return out

    def fetch(ticker: str) -> list[dict[str, Any]]:
        if not cik_map:
            cik_map.update(_load_cik_map())
        cik = cik_map.get(ticker.upper())
        if cik is None:
            return []
        payload = _fetch_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        out: list[dict[str, Any]] = []
        for i, form in enumerate(forms):
            out.append(
                {
                    "form": form,
                    "filingDate": dates[i] if i < len(dates) else "",
                    "accessionNumber": accessions[i] if i < len(accessions) else "",
                    "primaryDocument": docs[i] if i < len(docs) else "",
                    "cik": cik.lstrip("0") or "0",
                }
            )
        return out

    return fetch


class EdgarDataSource:
    """Real adapter backed by SEC EDGAR for ticker filings.

    Emits one `kind="filing"` DataPoint per recent 10-K / 10-Q / 8-K
    filing (forms and limit configurable). Network and parse errors
    return an empty list so a flaky ticker can't kill the Researcher
    run, matching the YFinanceDataSource contract.

    The default fetcher hard-requires the `TRADERS_EDGAR_UA` env var
    (a real contact string like "Acme Research user@acme.com") - SEC
    blocks requests without an identifying User-Agent. Tests inject a
    `filings_fn` to bypass the network entirely.
    """

    DEFAULT_FORMS: tuple[str, ...] = ("10-K", "10-Q", "8-K")
    DEFAULT_LIMIT: int = 5

    # Forms whose body is worth excerpting (10-K/10-Q carry Risk Factors / MD&A;
    # an 8-K is short and event-specific, so we leave its snippet as metadata).
    _TEXT_FORMS: tuple[str, ...] = ("10-K", "10-Q")
    _SECTION_LABELS: tuple[str, ...] = (
        "Item 1A. Risk Factors",
        "Risk Factors",
        "Item 7. Management's Discussion and Analysis",
        "Management's Discussion and Analysis",
    )

    def __init__(
        self,
        filings_fn: Callable[[str], list[dict[str, Any]]] | None = None,
        forms: tuple[str, ...] | None = None,
        limit: int | None = None,
        document_fetcher: Callable[[str], str] | None = None,
    ) -> None:
        if filings_fn is None:
            filings_fn = _default_edgar_fetcher()
        self._filings_fn = filings_fn
        self._forms = tuple(forms) if forms is not None else self.DEFAULT_FORMS
        self._limit = limit if limit is not None else self.DEFAULT_LIMIT
        # When set (slice 48 / B12), 10-K/10-Q snippets carry a real section
        # excerpt fetched from the primary document, not just the filing headline.
        self._document_fetcher = document_fetcher

    def fetch(self, ticker: str) -> list[DataPoint]:
        try:
            raw = self._filings_fn(ticker)
        except Exception as e:
            logger.warning("EDGAR filings fetch failed for %s: %s", ticker, e)
            return []
        if not raw:
            return []
        points: list[DataPoint] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            form = item.get("form")
            if form not in self._forms:
                continue
            points.append(self._to_data_point(ticker, str(form), item))
            if len(points) >= self._limit:
                break
        return points

    def _to_data_point(self, ticker: str, form: str, item: dict[str, Any]) -> DataPoint:
        filing_date = str(item.get("filingDate") or "")[:10]
        accession = str(item.get("accessionNumber") or "")
        primary_doc = str(item.get("primaryDocument") or "")
        cik = str(item.get("cik") or "")
        if accession and primary_doc and cik:
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                f"{accession.replace('-', '')}/{primary_doc}"
            )
        elif cik:
            url = (
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"
            )
        else:
            url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}"
        published = filing_date or date.today().isoformat()
        snippet = f"{ticker} filed {form} on {published}."
        excerpt = self._document_excerpt(form, url)
        if excerpt:
            snippet = f"{snippet} {excerpt}"
        return DataPoint(
            kind="filing",
            title=f"{ticker} {form}",
            url=url,
            snippet=snippet,
            published_at=published,
        )

    def _document_excerpt(self, form: str, url: str) -> str | None:
        """A Risk-Factors / MD&A excerpt from the primary document, or None.

        Only when a ``document_fetcher`` is configured and the form is worth
        excerpting; any fetch/parse error degrades silently to the metadata
        snippet, honoring the no-data-is-not-an-exception contract.
        """
        if self._document_fetcher is None or form not in self._TEXT_FORMS or not url:
            return None
        try:
            text = self._document_fetcher(url)
        except Exception:
            return None
        from traders.filing_text import extract_item

        return extract_item(text or "", *self._SECTION_LABELS)


def _default_edgar_document_fetcher() -> Callable[[str], str]:
    """Build the real primary-document fetcher (URL -> plain text), UA-gated.

    Network only when called; reuses ``TRADERS_EDGAR_UA``. Strips the fetched
    HTML to text via :func:`traders.filing_text.extract_text`.
    """
    import os
    import urllib.request

    ua = os.environ.get("TRADERS_EDGAR_UA", "").strip()
    if not ua:
        raise RuntimeError(
            "TRADERS_EDGAR_UA env var is required for the EDGAR data source. "
            "Set it to a real contact string (e.g. 'Acme Research user@acme.com')."
        )

    def fetch(url: str) -> str:
        from traders.filing_text import extract_text

        from traders.net import MAX_FILING_BYTES, read_capped

        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (vetted SEC URLs)
            return extract_text(read_capped(resp, MAX_FILING_BYTES).decode("utf-8", "replace"))

    return fetch


def make_data_source(name: str) -> DataSource:
    """Build a DataSource by name. Called from the CLI / orchestrator."""
    if name == "stub":
        return StubDataSource()
    if name == "yfinance":
        return YFinanceDataSource()
    if name == "edgar":
        return EdgarDataSource()
    if name == "edgar-full":
        # Like 'edgar', but enriches 10-K/10-Q snippets with a real section excerpt.
        return EdgarDataSource(document_fetcher=_default_edgar_document_fetcher())
    raise ValueError(
        f"unknown data source: {name!r} (expected 'stub', 'yfinance', 'edgar', or 'edgar-full')"
    )
