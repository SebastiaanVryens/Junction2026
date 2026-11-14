-- People waiting for an answer (the Lens, Ask the network) go before background Nemotron work.
-- NVIDIA answers 429 when two requests on one key overlap, and the investigator is busy most of
-- the time; while `until` is in the future, background services start no new model calls.
CREATE TABLE IF NOT EXISTS nemotron_priority (
    id     integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    until  timestamptz NOT NULL
);
