-- Reviews made in random-sample mode: the cluster was drawn at random from all tiers, not served
-- highest score first. Only these give an unbiased precision and false-positive rate. Team request,
-- 14 Nov 2026.
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS sampled boolean NOT NULL DEFAULT false;
