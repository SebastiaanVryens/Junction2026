-- M2: candidate clusters from stage 1, account profiles from stage 2, scored signals.
-- Privacy (CLAUDE.md §7): accounts are keyed by an HMAC of their DID. The raw DID and post
-- texts are kept only while a reviewer may need them and are removed after 72 hours by
-- factslens.common.store.apply_retention.

CREATE TABLE clusters (
    id              text PRIMARY KEY,          -- stage-1 group key, e.g. 'text:9f2c…'
    kind            text NOT NULL,             -- text | link | repost
    label           text NOT NULL,             -- the shared text, link or reposted post
    first_seen      timestamptz NOT NULL,
    last_seen       timestamptz NOT NULL,
    candidate_since timestamptz NOT NULL DEFAULT now(),
    accounts        integer NOT NULL,
    events          integer NOT NULL,
    stats           jsonb NOT NULL,            -- stage-1 statistics (burst, copy, ring shares, ...)
    per_minute      integer[] NOT NULL,
    domains         jsonb NOT NULL DEFAULT '[]',
    languages       jsonb NOT NULL DEFAULT '[]',
    signals         jsonb,                     -- stage-2 signals with values and thresholds
    score           real,
    tier            text,                      -- pending | likely_organic | watch | suspicious
    scored_at       timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX clusters_last_seen ON clusters (last_seen DESC);
CREATE INDEX clusters_score ON clusters (score DESC NULLS LAST);
CREATE INDEX clusters_needs_scoring ON clusters (updated_at) WHERE scored_at IS NULL OR scored_at < updated_at;

CREATE TABLE cluster_members (
    cluster_id   text NOT NULL REFERENCES clusters (id) ON DELETE CASCADE,
    account_hash text NOT NULL,
    did          text,                         -- raw id, removed after 72 h
    events       integer NOT NULL,
    first_seen   timestamptz NOT NULL,
    last_seen    timestamptz NOT NULL,
    PRIMARY KEY (cluster_id, account_hash)
);
CREATE INDEX cluster_members_account ON cluster_members (account_hash);

CREATE TABLE cluster_posts (
    cluster_id   text NOT NULL REFERENCES clusters (id) ON DELETE CASCADE,
    post_id      text NOT NULL,                -- at:// URI, removed with the text after 72 h
    account_hash text NOT NULL,
    posted_at    timestamptz NOT NULL,
    text         text,
    bridged      boolean NOT NULL DEFAULT false,
    media        integer NOT NULL DEFAULT 0,
    PRIMARY KEY (cluster_id, post_id)
);

CREATE TABLE accounts (
    account_hash text PRIMARY KEY,
    did          text,                         -- raw id, removed after 72 h without new activity
    handle       text,
    display_name text,
    description  text,
    created_at   timestamptz,                  -- account creation time reported by Bluesky
    followers    integer,
    follows      integer,
    posts        integer,
    has_avatar   boolean,
    has_banner   boolean,
    labels       text[] NOT NULL DEFAULT '{}',
    status       text,                         -- active | unavailable
    fetched_at   timestamptz,
    last_seen    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX accounts_unfetched ON accounts (last_seen DESC) WHERE fetched_at IS NULL;
