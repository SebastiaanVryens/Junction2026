-- M3: what the investigator needs from each sample post, Nemotron reports, a log of every
-- model call, and reviewer decisions.

ALTER TABLE cluster_posts
    ADD COLUMN media_refs jsonb NOT NULL DEFAULT '[]',   -- [{kind: image|video|external, ref}]
    ADD COLUMN links text[] NOT NULL DEFAULT '{}';

CREATE TABLE investigations (
    id              bigserial PRIMARY KEY,
    cluster_id      text NOT NULL REFERENCES clusters (id) ON DELETE CASCADE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    accounts_at     integer NOT NULL,                  -- cluster size when investigated
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    status          text NOT NULL,                     -- done | failed
    verdict         text,                              -- staged | organic | insufficient_evidence
    report          jsonb,                             -- validated report (factslens.investigator.report)
    evidence        jsonb NOT NULL DEFAULT '{}',       -- what was shown to the model (refs, not content)
    calls           integer NOT NULL DEFAULT 0,
    prompt_tokens   integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    latency_ms      integer NOT NULL DEFAULT 0,
    error           text
);
CREATE INDEX investigations_cluster ON investigations (cluster_id, created_at DESC);

CREATE TABLE model_calls (
    id               bigserial PRIMARY KEY,
    at               timestamptz NOT NULL DEFAULT now(),
    purpose          text NOT NULL,                    -- e.g. investigate:tools, investigate:report
    cluster_id       text,
    provider         text NOT NULL,
    model            text NOT NULL,
    status           text NOT NULL,                    -- ok | http_<code> | error
    attempts         integer NOT NULL,
    latency_ms       integer NOT NULL,
    prompt_tokens    integer,
    completion_tokens integer,
    reasoning_tokens integer,
    error            text
);
CREATE INDEX model_calls_at ON model_calls (at DESC);

CREATE TABLE reviews (
    id          bigserial PRIMARY KEY,
    cluster_id  text NOT NULL REFERENCES clusters (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    decision    text NOT NULL CHECK (decision IN ('confirm', 'false_alarm', 'unsure')),
    note        text,
    tier_at     text,                                  -- what FactsLens said when the reviewer decided
    verdict_at  text                                   -- what Nemotron said when the reviewer decided
);
CREATE INDEX reviews_cluster ON reviews (cluster_id, created_at DESC);
