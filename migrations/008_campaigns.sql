-- Campaign reports: Nemotron links clusters that share accounts into one operation
-- (factslens.investigator.campaigns). Evidence maps ids to cluster ids and account hashes only;
-- reports can quote posts, so rows are deleted after 72 hours with the other raw content.
CREATE TABLE IF NOT EXISTS campaigns (
    id                bigserial PRIMARY KEY,
    campaign_key      text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    cluster_ids       text[] NOT NULL,
    accounts          integer NOT NULL,
    status            text NOT NULL,                 -- done | failed
    title             text,
    kind              text,                          -- propaganda | bots | spam | organic | unclear
    verdict           text,
    report            jsonb,
    evidence          jsonb NOT NULL DEFAULT '{}',
    model             text,
    prompt_version    text,
    calls             integer NOT NULL DEFAULT 0,
    prompt_tokens     integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    latency_ms        integer NOT NULL DEFAULT 0,
    error             text
);
CREATE INDEX IF NOT EXISTS campaigns_key ON campaigns (campaign_key, created_at DESC);
CREATE INDEX IF NOT EXISTS campaigns_created ON campaigns (created_at DESC);
CREATE INDEX IF NOT EXISTS campaigns_clusters ON campaigns USING gin (cluster_ids);
