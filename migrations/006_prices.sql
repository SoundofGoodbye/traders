-- Slice 17 (Backtest harness): historical daily-close store. The harness
-- replays the pipeline over these prices to evaluate a parameter set against
-- months of data. One row per (ticker, day); INSERT OR REPLACE keeps the
-- latest close for a day. Populating it from a real source (yfinance) is a
-- data-collection concern deferred to the improvement plan; the harness also
-- ships a deterministic synthetic source needing no rows at all.

CREATE TABLE prices (
    ticker TEXT NOT NULL,
    day TEXT NOT NULL,        -- ISO date 'YYYY-MM-DD'
    close REAL NOT NULL,
    PRIMARY KEY (ticker, day)
);

CREATE INDEX idx_prices_day ON prices(day);
