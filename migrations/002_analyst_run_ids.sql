-- Slice 3 (Analyst): track the analyst run that produced each thesis,
-- and the research run it consumed. Parallels candidates.scout_run_id
-- and research_notes.run_id.

ALTER TABLE theses ADD COLUMN run_id INTEGER;
ALTER TABLE theses ADD COLUMN research_run_id INTEGER;

CREATE INDEX idx_theses_run_id ON theses(run_id);
CREATE INDEX idx_theses_research_run_id ON theses(research_run_id);
