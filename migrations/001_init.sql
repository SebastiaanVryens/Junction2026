-- M1: ingest cursor and service status. Applied once, in file-name order, by factslens.common.db.migrate.

CREATE TABLE ingest_cursor (
    source      text PRIMARY KEY,
    cursor      bigint NOT NULL,          -- source position (Jetstream: microseconds since the epoch)
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE service_status (
    service     text PRIMARY KEY,         -- 'ingest', 'detector', ...
    updated_at  timestamptz NOT NULL DEFAULT now(),
    data        jsonb NOT NULL            -- aggregate counters only; never account identifiers
);
