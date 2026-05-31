-- Slice 16 (Self-improvement loop): append-only ledger of optimizer
-- proposals. Each row is one single-variable change to the learned
-- parameters, with the baseline it was measured against and a status the
-- operator advances (proposed -> applied | rejected). Rows are never
-- edited except to record the decision; history stays auditable.

CREATE TABLE experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    param TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    baseline_verdict TEXT NOT NULL,
    baseline_metrics TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    decided_at TEXT
);

CREATE INDEX idx_experiments_status ON experiments(status);
