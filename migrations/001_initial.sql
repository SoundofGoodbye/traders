-- Initial schema for the traders database.
-- Applied in order; never edit a shipped migration. Add a new file for changes.

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    scout_run_id INTEGER NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_candidates_ticker ON candidates(ticker);

CREATE TABLE research_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    sources TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_research_notes_ticker ON research_notes(ticker);

CREATE TABLE theses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    thesis_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    conviction INTEGER NOT NULL,
    suggested_size_pct REAL NOT NULL,
    exit_condition TEXT,
    rationale TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE INDEX idx_theses_ticker ON theses(ticker);
CREATE INDEX idx_theses_status ON theses(status);

CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    thesis_id INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    entry_price REAL,
    exit_price REAL,
    size_pct REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE INDEX idx_positions_ticker ON positions(ticker);
CREATE INDEX idx_positions_thesis_id ON positions(thesis_id);
CREATE INDEX idx_positions_status ON positions(status);

CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    notes TEXT,
    reported_at TEXT NOT NULL
);

CREATE INDEX idx_feedback_thesis_id ON feedback(thesis_id);

CREATE TABLE post_mortems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    reviewer_run_id INTEGER NOT NULL,
    outcome TEXT,
    lessons TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_post_mortems_position_id ON post_mortems(position_id);
