-- Recycled pictures (factslens.investigator.recycled): a perceptual hash per image in our clusters,
-- and pairs where the same picture was posted again by another account with a different caption,
-- with Nemotron's judgement of which caption the picture fits. Both hold post ids and captions, so
-- they are deleted after 72 hours with the other raw content.
CREATE TABLE IF NOT EXISTS image_hashes (
    post_id      text NOT NULL,
    media_ref    text NOT NULL,
    cluster_id   text NOT NULL,
    account_hash text NOT NULL,
    posted_at    timestamptz NOT NULL,
    caption      text,
    source       text,
    hash         bigint,                       -- NULL when the image could not be fetched
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, media_ref)
);
CREATE INDEX IF NOT EXISTS image_hashes_created ON image_hashes (created_at DESC);

CREATE TABLE IF NOT EXISTS image_reuse (
    id               bigserial PRIMARY KEY,
    created_at       timestamptz NOT NULL DEFAULT now(),
    media_ref        text NOT NULL,
    hash_distance    integer NOT NULL,
    earlier_post     text NOT NULL,
    earlier_cluster  text NOT NULL,
    earlier_at       timestamptz NOT NULL,
    earlier_caption  text,
    earlier_source   text,
    later_post       text NOT NULL,
    later_cluster    text NOT NULL,
    later_at         timestamptz NOT NULL,
    later_caption    text,
    later_source     text,
    status           text NOT NULL,            -- done | failed
    supports         text,                     -- earlier | later | both | neither | cannot_tell
    judgement        jsonb,
    model            text,
    prompt_version   text,
    UNIQUE (earlier_post, later_post)
);
CREATE INDEX IF NOT EXISTS image_reuse_later ON image_reuse (later_cluster);
CREATE INDEX IF NOT EXISTS image_reuse_earlier ON image_reuse (earlier_cluster);
