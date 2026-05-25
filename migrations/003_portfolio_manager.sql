-- Slice 4 (Portfolio Manager): per-thesis decisions from the daily PM run.
-- Each row records whether a thesis was accepted (forwarded in the daily
-- report) or rejected (filtered out by concentration / correlation checks),
-- with the human-readable reason for the decision.

CREATE TABLE pm_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pm_run_id INTEGER NOT NULL,
    thesis_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_pm_decisions_pm_run_id ON pm_decisions(pm_run_id);
CREATE INDEX idx_pm_decisions_thesis_id ON pm_decisions(thesis_id);
CREATE INDEX idx_pm_decisions_decision ON pm_decisions(decision);
