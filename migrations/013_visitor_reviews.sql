-- Anyone may review from 15 Nov 2026 (team decision: no reviewer key for the judging day).
-- Reviews by visitors are kept apart: they never move our published figures and never reach
-- Nemotron's prompt. Reviews by the team (logged in) stay by_visitor = false.
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS by_visitor boolean NOT NULL DEFAULT false;
