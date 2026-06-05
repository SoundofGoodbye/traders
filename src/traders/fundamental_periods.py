"""Period-by-period fundamentals — historical statement lines per fiscal period.

The point-in-time *series* the slice-25 snapshot table (``fundamentals``)
explicitly could not provide: one row per (ticker, fiscal period, statement
cadence), holding the raw income / balance-sheet / cash-flow lines that quality
and value-depth work needs (Piotroski F-score, ROIC and margin trends, owner
earnings) — none of which is computable from a single ``.info`` snapshot.

Shape mirrors :mod:`traders.fundamentals`: a typed ``FundamentalPeriod`` row,
idempotent ``save_periods`` / ``load_periods``, a look-ahead-safe accessor, and
``ingest_fundamental_periods`` behind an injected ``fetch_fn`` (real impl =
yfinance statements, behind the ``realdata`` extra; tests inject canned data).

Look-ahead safety (premortem guardrail #3): a statement for ``period_end`` is not
public until it is *filed*, weeks-to-months later. ``available_at`` records the
filing date when the source provides it; when it is absent, ``availability_date``
estimates it as ``period_end`` + a conservative reporting lag (annual/quarterly),
and ``load_periods_asof`` / ``latest_periods`` hide any period not yet available
as of the query date. Ratios are NOT stored — derived figures join these raw lines
(and, where a price is needed, the ``prices`` close), so nothing bakes in a stale
input.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from typing import Any, Callable

# Conservative default reporting lags: how long after a fiscal period ends a
# statement typically becomes public, used only when a real filing date is
# absent. SEC large-filer deadlines are 60d (10-K) / 40d (10-Q); these pad them
# so the estimate errs toward *hiding* a period rather than leaking it.
ANNUAL_REPORTING_LAG_DAYS = 90
QUARTERLY_REPORTING_LAG_DAYS = 45


@dataclass(frozen=True)
class FundamentalPeriod:
    """One fiscal period's raw statement lines for a ticker."""

    ticker: str
    period_end: str  # ISO date the fiscal period ended
    period_type: str  # 'annual' | 'quarterly'
    currency: str | None
    revenue: float | None
    gross_profit: float | None
    net_income: float | None
    operating_cash_flow: float | None
    capital_expenditure: float | None
    total_assets: float | None
    current_assets: float | None
    current_liabilities: float | None
    long_term_debt: float | None
    total_equity: float | None
    shares_outstanding: float | None
    available_at: str | None = None  # ISO filing/publish date, when known
    source: str = "yfinance"


# Column order for the table — derived from the dataclass so the SELECT, the
# INSERT, and ``FundamentalPeriod(*row)`` can never drift apart.
_FIELDS = tuple(f.name for f in fields(FundamentalPeriod))
_SELECT = f"SELECT {', '.join(_FIELDS)} FROM fundamental_periods"

# The numeric statement lines — used to drop a period that carries no usable
# figure at all (everything but the identifying / date / source columns).
_NUMERIC_FIELDS = (
    "revenue",
    "gross_profit",
    "net_income",
    "operating_cash_flow",
    "capital_expenditure",
    "total_assets",
    "current_assets",
    "current_liabilities",
    "long_term_debt",
    "total_equity",
    "shares_outstanding",
)


def _num(value: Any) -> float | None:
    """Coerce a value to float, or None when it isn't a real number (NaN dropped)."""
    if isinstance(value, bool):  # bool is an int subclass — not a metric
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if f != f else f  # f != f is True only for NaN
    return None


def _iso_date(value: Any) -> str | None:
    """Coerce a value (str / date / datetime / pandas Timestamp) to an ISO date.

    Returns None when it isn't a parseable date. datetime is checked before date
    because datetime subclasses date (and pandas Timestamp subclasses datetime).
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            return None
    return None


def periods_from_statements(
    ticker: str,
    raw_periods: list[dict[str, Any]],
    *,
    source: str = "yfinance",
) -> list[FundamentalPeriod]:
    """Map normalized per-period statement dicts onto ``FundamentalPeriod`` rows.

    Each input dict carries ``period_end`` (required), ``period_type``
    (annual/quarterly), an optional ``available_at`` filing date, and any of the
    numeric statement lines. A period is dropped when its date is unparseable, its
    cadence is unknown, or it carries no usable numeric field — so a flaky or
    placeholder period is skipped rather than stored as noise.
    """
    out: list[FundamentalPeriod] = []
    for raw in raw_periods:
        if not isinstance(raw, dict):
            continue
        period_end = _iso_date(raw.get("period_end"))
        if period_end is None:
            continue
        period_type = str(raw.get("period_type") or "annual").lower()
        if period_type not in ("annual", "quarterly"):
            continue
        row = FundamentalPeriod(
            ticker=ticker,
            period_end=period_end,
            period_type=period_type,
            currency=(raw.get("currency") or None),
            revenue=_num(raw.get("revenue")),
            gross_profit=_num(raw.get("gross_profit")),
            net_income=_num(raw.get("net_income")),
            operating_cash_flow=_num(raw.get("operating_cash_flow")),
            capital_expenditure=_num(raw.get("capital_expenditure")),
            total_assets=_num(raw.get("total_assets")),
            current_assets=_num(raw.get("current_assets")),
            current_liabilities=_num(raw.get("current_liabilities")),
            long_term_debt=_num(raw.get("long_term_debt")),
            total_equity=_num(raw.get("total_equity")),
            shares_outstanding=_num(raw.get("shares_outstanding")),
            available_at=_iso_date(raw.get("available_at")),
            source=source,
        )
        if all(getattr(row, name) is None for name in _NUMERIC_FIELDS):
            continue
        out.append(row)
    return out


def availability_date(
    period: FundamentalPeriod,
    *,
    annual_lag_days: int = ANNUAL_REPORTING_LAG_DAYS,
    quarterly_lag_days: int = QUARTERLY_REPORTING_LAG_DAYS,
) -> str:
    """The ISO date a period becomes public: its filing date, else an estimate.

    Uses ``available_at`` when the source recorded it; otherwise falls back to
    ``period_end`` + a conservative cadence-specific reporting lag. This is the
    single gate that makes the as-of accessors look-ahead-safe.
    """
    if period.available_at:
        return period.available_at
    lag = quarterly_lag_days if period.period_type == "quarterly" else annual_lag_days
    return (date.fromisoformat(period.period_end) + timedelta(days=lag)).isoformat()


def save_periods(conn: sqlite3.Connection, rows: list[FundamentalPeriod]) -> int:
    """Upsert periods, idempotent on ``(ticker, period_end, period_type, source)``.

    Returns the number of rows written.
    """
    payload = [tuple(getattr(r, name) for name in _FIELDS) for r in rows]
    conn.executemany(
        f"INSERT OR REPLACE INTO fundamental_periods ({', '.join(_FIELDS)})"
        f" VALUES ({', '.join('?' * len(_FIELDS))})",
        payload,
    )
    conn.commit()
    return len(payload)


def load_periods(
    conn: sqlite3.Connection,
    *,
    ticker: str | None = None,
    source: str | None = None,
    period_type: str | None = None,
) -> list[FundamentalPeriod]:
    """Load periods (optionally filtered), newest ``period_end`` first."""
    clauses: list[str] = []
    params: list[object] = []
    if ticker is not None:
        clauses.append("ticker = ?")
        params.append(ticker)
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if period_type is not None:
        clauses.append("period_type = ?")
        params.append(period_type)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(f"{_SELECT}{where} ORDER BY period_end DESC, ticker", params).fetchall()
    return [FundamentalPeriod(*r) for r in rows]


def load_periods_asof(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    ticker: str | None = None,
    source: str | None = None,
    period_type: str | None = None,
    annual_lag_days: int = ANNUAL_REPORTING_LAG_DAYS,
    quarterly_lag_days: int = QUARTERLY_REPORTING_LAG_DAYS,
) -> list[FundamentalPeriod]:
    """Periods publicly available on or before ``as_of`` (look-ahead-safe), newest first.

    A period is included only when its :func:`availability_date` (filing date, or
    the conservative reporting-lag estimate) is on or before ``as_of`` — so a
    historical rebalance can't peek at a statement filed later.
    """
    return [
        r
        for r in load_periods(conn, ticker=ticker, source=source, period_type=period_type)
        if availability_date(
            r, annual_lag_days=annual_lag_days, quarterly_lag_days=quarterly_lag_days
        )
        <= as_of
    ]


def latest_periods(
    conn: sqlite3.Connection,
    ticker: str,
    *,
    as_of: str | None = None,
    period_type: str | None = None,
    limit: int | None = None,
    annual_lag_days: int = ANNUAL_REPORTING_LAG_DAYS,
    quarterly_lag_days: int = QUARTERLY_REPORTING_LAG_DAYS,
) -> list[FundamentalPeriod]:
    """The most recent periods for ``ticker``, newest first (optionally as-of-gated).

    The convenience accessor a quality/trend consumer reaches for: the current and
    prior periods needed for a year-over-year delta. ``as_of`` makes it
    look-ahead-safe; ``limit`` caps how many periods come back.
    """
    if as_of is None:
        rows = load_periods(conn, ticker=ticker, period_type=period_type)
    else:
        rows = load_periods_asof(
            conn,
            as_of=as_of,
            ticker=ticker,
            period_type=period_type,
            annual_lag_days=annual_lag_days,
            quarterly_lag_days=quarterly_lag_days,
        )
    return rows if limit is None else rows[:limit]


# --- yfinance statements fetcher (real impl; network only, behind `realdata`) ---

# yfinance statement-label aliases -> our normalized keys. yfinance normalizes
# Yahoo's line items, but the exact label varies by company/version, so we try a
# few per field and take the first present. Exercised only on real runs; the
# tested surface injects already-normalized dicts.
_YF_LABELS: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Operating Revenue", "Revenue"),
    "gross_profit": ("Gross Profit",),
    "net_income": (
        "Net Income",
        "Net Income Common Stockholders",
        "Net Income Continuous Operations",
    ),
    "operating_cash_flow": (
        "Operating Cash Flow",
        "Cash Flow From Continuing Operating Activities",
        "Total Cash From Operating Activities",
    ),
    "capital_expenditure": ("Capital Expenditure", "Capital Expenditures"),
    "total_assets": ("Total Assets",),
    "current_assets": ("Current Assets", "Total Current Assets"),
    "current_liabilities": ("Current Liabilities", "Total Current Liabilities"),
    "long_term_debt": ("Long Term Debt",),
    "total_equity": (
        "Stockholders Equity",
        "Total Stockholder Equity",
        "Common Stock Equity",
    ),
    "shares_outstanding": ("Share Issued", "Ordinary Shares Number"),
}

_YF_FRAMES: dict[str, tuple[str, str, str]] = {
    "annual": ("income_stmt", "balance_sheet", "cashflow"),
    "quarterly": ("quarterly_income_stmt", "quarterly_balance_sheet", "quarterly_cashflow"),
}


def _stmt_cell(df: Any, labels: tuple[str, ...], col: Any) -> float | None:
    """First present, numeric cell among ``labels`` for the period column ``col``."""
    if df is None:
        return None
    for label in labels:
        try:
            value = df.loc[label, col]
        except (KeyError, TypeError):
            continue
        num = _num(value)
        if num is not None:
            return num
    return None


def _statements_to_periods(ticker_obj: Any, period_types: tuple[str, ...]) -> list[dict[str, Any]]:
    """Merge a yfinance Ticker's income / balance / cash-flow frames into the
    normalized per-period dicts ``periods_from_statements`` consumes.
    """
    out: list[dict[str, Any]] = []
    for ptype in period_types:
        attrs = _YF_FRAMES.get(ptype)
        if attrs is None:
            continue
        dfs = [getattr(ticker_obj, attr, None) for attr in attrs]
        seen: set[Any] = set()
        cols: list[Any] = []
        for df in dfs:
            for col in list(getattr(df, "columns", []) or []):
                if col not in seen:
                    seen.add(col)
                    cols.append(col)
        for col in cols:
            period_end = _iso_date(col)
            if period_end is None:
                continue
            period: dict[str, Any] = {"period_end": period_end, "period_type": ptype}
            for key, labels in _YF_LABELS.items():
                value = None
                for df in dfs:
                    value = _stmt_cell(df, labels, col)
                    if value is not None:
                        break
                period[key] = value
            out.append(period)
    return out


def _default_yf_statements_fetcher(
    period_types: tuple[str, ...] = ("annual", "quarterly"),
) -> Callable[[str], list[dict[str, Any]]]:
    """Build the real yfinance statements fetcher (ticker -> normalized periods).

    Network only when called. Hard-requires the ``realdata`` extra — mirrors how
    the snapshot ingestor and ``YFinanceDataSource`` raise when yfinance is absent.
    """
    try:
        import yfinance
    except ImportError as e:
        raise ImportError(
            "yfinance is not installed. Install with: uv sync --extra realdata"
        ) from e

    def fetch(ticker: str) -> list[dict[str, Any]]:
        return _statements_to_periods(yfinance.Ticker(ticker), period_types)

    return fetch


def ingest_fundamental_periods(
    conn: sqlite3.Connection,
    tickers: list[str],
    *,
    fetch_fn: Callable[[str], list[dict[str, Any]]] | None = None,
    source: str = "yfinance",
    delay_s: float = 0.0,
) -> dict[str, object]:
    """Fetch statement history for each ticker into the ``fundamental_periods`` table.

    Returns ``{"written": [tickers], "skipped": [tickers], "periods": n}``. A
    ticker is skipped (never fatal) when the fetch raises or yields no usable
    period. ``delay_s`` throttles between *network* fetches (set it for real
    yfinance runs; defaults to 0 so injected-fetcher tests stay fast).
    """
    fetch = fetch_fn or _default_yf_statements_fetcher()
    written: list[str] = []
    skipped: list[str] = []
    all_rows: list[FundamentalPeriod] = []
    for ticker in tickers:
        if delay_s > 0:
            import time

            time.sleep(delay_s)
        try:
            raw = fetch(ticker)
        except Exception:
            skipped.append(ticker)
            continue
        rows = periods_from_statements(ticker, list(raw or []), source=source)
        if not rows:
            skipped.append(ticker)
            continue
        all_rows.extend(rows)
        written.append(ticker)
    if all_rows:
        save_periods(conn, all_rows)
    return {"written": written, "skipped": skipped, "periods": len(all_rows)}
