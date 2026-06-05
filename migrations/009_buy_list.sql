-- Slice 37 (B5): a user-curated buy-list — names you'd own at your price, each
-- with a target buy-below price. One row per ticker; INSERT OR REPLACE upserts
-- (the writer preserves created_at on update). This is *user data*, never written
-- by the agents — it flips the workflow from "react to today's candidate list"
-- (which nudges overtrading) to "wait for your price", the persona review's
-- highest-leverage behavioural change. It pairs with the slice-36 intrinsic-value
-- buy-below as a suggested target.

CREATE TABLE buy_list (
    ticker TEXT PRIMARY KEY,
    target_price REAL NOT NULL,          -- act when the price is at or below this
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
