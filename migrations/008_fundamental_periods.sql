-- Slice 33 (Period-by-period fundamentals): historical statement lines per ticker
-- and fiscal period — the point-in-time *series* the slice-25 snapshot table
-- (`fundamentals`) explicitly could not provide. One row per
-- (ticker, period_end, period_type, source); INSERT OR REPLACE keeps a re-fetch
-- idempotent.
--
-- These raw income / balance-sheet / cash-flow lines are the inputs the deferred
-- quality / value-depth work needs (Piotroski F-score, ROIC and margin trends,
-- owner earnings) — none of which a single `.info` snapshot can support.
--
-- Look-ahead safety: a statement for `period_end` is not public until it is
-- *filed*, weeks-to-months later. `available_at` records the filing/publish date
-- when the source provides it; when it is NULL the look-ahead-safe accessor
-- estimates availability as period_end + a conservative reporting lag (see
-- traders.fundamental_periods). Ratios are NOT stored — derived figures join these
-- raw lines (and, where a price is needed, the `prices` close), so nothing bakes
-- in a stale input.

CREATE TABLE fundamental_periods (
    ticker TEXT NOT NULL,
    period_end TEXT NOT NULL,            -- ISO date the fiscal period ended
    period_type TEXT NOT NULL,           -- 'annual' | 'quarterly'
    currency TEXT,
    revenue REAL,
    gross_profit REAL,
    net_income REAL,
    operating_cash_flow REAL,
    capital_expenditure REAL,
    total_assets REAL,
    current_assets REAL,
    current_liabilities REAL,
    long_term_debt REAL,
    total_equity REAL,
    shares_outstanding REAL,
    available_at TEXT,                   -- ISO filing/publish date, when known
    source TEXT NOT NULL DEFAULT 'yfinance',
    PRIMARY KEY (ticker, period_end, period_type, source)
);

CREATE INDEX idx_fundamental_periods_ticker ON fundamental_periods(ticker, period_end);
