-- Accounts on a public list (IFTAS suspected Portal Kombat accounts) are shown by name on the
-- public site; set by factslens.enrich when it fetches profiles. Team decision, 14 Nov 2026.
ALTER TABLE accounts ADD COLUMN on_public_list boolean NOT NULL DEFAULT false;
