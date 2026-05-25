-- Slice 6 (Feedback loop): extend `feedback` with the executed price, the
-- actual size taken (may differ from thesis.suggested_size_pct on partials),
-- and a pointer to the position this event opened or closed. All three are
-- nullable because `skip` events carry none of them.

ALTER TABLE feedback ADD COLUMN price REAL;
ALTER TABLE feedback ADD COLUMN size_pct REAL;
ALTER TABLE feedback ADD COLUMN position_id INTEGER;

CREATE INDEX idx_feedback_position_id ON feedback(position_id);
