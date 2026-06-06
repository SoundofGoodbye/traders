-- Security hardening (audit M3): add value/status CHECK constraints to positions
-- and theses at the schema level. SQLite cannot ALTER a table to add a CHECK, so
-- this rebuilds each table (create-new / copy / drop / rename) and recreates its
-- indexes. Append-only: 001 is never edited.
--
-- The bounds are already enforced in Python (feedback._validate_open and the
-- Analyst draft guard); this makes them a database invariant too, so no path —
-- a future generator, a direct INSERT, a manual fix — can persist a position the
-- exposure/PM math would trust blindly. Landed as its own migration once the
-- prior (disposable) position history no longer needed preserving.

-- positions ------------------------------------------------------------------
CREATE TABLE positions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    thesis_id INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    entry_price REAL CHECK (entry_price IS NULL OR entry_price > 0),
    exit_price REAL CHECK (exit_price IS NULL OR exit_price > 0),
    size_pct REAL NOT NULL CHECK (size_pct > 0 AND size_pct <= 100),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed'))
);
INSERT INTO positions_new
    (id, ticker, thesis_id, opened_at, closed_at, entry_price, exit_price, size_pct, status)
    SELECT id, ticker, thesis_id, opened_at, closed_at, entry_price, exit_price, size_pct, status
    FROM positions;
DROP TABLE positions;
ALTER TABLE positions_new RENAME TO positions;
CREATE INDEX idx_positions_ticker ON positions(ticker);
CREATE INDEX idx_positions_thesis_id ON positions(thesis_id);
CREATE INDEX idx_positions_status ON positions(status);
CREATE UNIQUE INDEX idx_positions_one_open_per_thesis ON positions(thesis_id) WHERE status = 'open';

-- theses ---------------------------------------------------------------------
CREATE TABLE theses_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    thesis_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    conviction INTEGER NOT NULL CHECK (conviction BETWEEN 1 AND 5),
    suggested_size_pct REAL NOT NULL CHECK (suggested_size_pct > 0 AND suggested_size_pct <= 100),
    exit_condition TEXT,
    rationale TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    run_id INTEGER,
    research_run_id INTEGER
);
INSERT INTO theses_new
    (id, ticker, thesis_type, direction, conviction, suggested_size_pct, exit_condition,
     rationale, created_at, status, run_id, research_run_id)
    SELECT id, ticker, thesis_type, direction, conviction, suggested_size_pct, exit_condition,
           rationale, created_at, status, run_id, research_run_id
    FROM theses;
DROP TABLE theses;
ALTER TABLE theses_new RENAME TO theses;
CREATE INDEX idx_theses_ticker ON theses(ticker);
CREATE INDEX idx_theses_status ON theses(status);
