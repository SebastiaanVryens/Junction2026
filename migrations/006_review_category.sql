-- What kind of coordinated inauthentic behaviour a reviewer confirmed (Hostinger's three: propaganda,
-- bot-driven amplification, other bad actors such as spam). Only set when decision = 'confirm';
-- every confirm still counts as confirmed in the precision metrics. Team request, 14 Nov 2026.
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS category text;
