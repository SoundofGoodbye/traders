-- Security hardening (audit M3): enforce "one open position per thesis" at the
-- database level. This backstops the check-then-insert in
-- feedback.record_fill / record_partial against a race (e.g. two concurrent web
-- POSTs both passing the Python guard before either inserts). The index only
-- covers open rows, so closing a position and re-entering the same thesis still
-- works.
CREATE UNIQUE INDEX idx_positions_one_open_per_thesis
    ON positions(thesis_id) WHERE status = 'open';
