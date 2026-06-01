-- Slice 25 (Fundamentals ingestion): point-in-time fundamental snapshots per
-- ticker — the raw inputs the slice-26 value / quality / catalyst signals derive
-- from. One row per (ticker, as_of, source); INSERT OR REPLACE keeps a re-fetch
-- idempotent.
--
-- Ratios are deliberately NOT stored: slice 26 derives E/P, B/P, FCF/P by joining
-- a snapshot's trailing_eps / book_value_per_share / free_cash_flow / market_cap
-- against the `prices` close, so a snapshot can't bake in a stale price.
--
-- Caveat (documented, not hidden): a yfinance `.info` snapshot is a *current*
-- reading, not a historical time series. ingest_fundamentals stamps it with an
-- `as_of` date, so a row is only point-in-time-valid for that date — true
-- historical fundamental backtests need period-by-period statements, deferred.

CREATE TABLE fundamentals (
    ticker TEXT NOT NULL,
    as_of TEXT NOT NULL,                 -- ISO date the snapshot was observed
    currency TEXT,
    market_cap REAL,
    trailing_eps REAL,                   -- trailing-twelve-month EPS
    book_value_per_share REAL,
    free_cash_flow REAL,
    shares_outstanding REAL,
    next_earnings_date TEXT,             -- ISO date, for catalyst / earnings proximity
    source TEXT NOT NULL DEFAULT 'yfinance',
    PRIMARY KEY (ticker, as_of, source)
);

CREATE INDEX idx_fundamentals_ticker ON fundamentals(ticker, as_of);
