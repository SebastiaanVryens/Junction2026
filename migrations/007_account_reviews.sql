-- Per-account exceptions to a cluster decision: a reviewer marks one account as a real person (in a
-- confirmed cluster) or as inauthentic (in a false-alarm cluster). Keyed by the account's HMAC, so no
-- raw id is stored. The latest mark per account counts, across clusters. Team request, 14 Nov 2026.
CREATE TABLE IF NOT EXISTS account_reviews (
    id            bigserial PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    account_hash  text NOT NULL,
    cluster_id    text NOT NULL,
    decision      text NOT NULL CHECK (decision IN ('real', 'inauthentic', 'clear'))
);
CREATE INDEX IF NOT EXISTS account_reviews_account ON account_reviews (account_hash, created_at DESC);
