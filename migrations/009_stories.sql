-- Contested stories: how state media, Telegram channels, independent outlets and our social
-- clusters tell the same event (factslens.investigator.stories). Deleted after 72 hours with the
-- other raw content, because the evidence quotes posts.
CREATE TABLE IF NOT EXISTS stories (
    id                bigserial PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    status            text NOT NULL,             -- done | failed
    title             text,
    story_status      text,                      -- contested | consistent | only_state_media | insufficient_coverage
    cluster_ids       text[] NOT NULL DEFAULT '{}',
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
CREATE INDEX IF NOT EXISTS stories_created ON stories (created_at DESC);
CREATE INDEX IF NOT EXISTS stories_clusters ON stories USING gin (cluster_ids);
