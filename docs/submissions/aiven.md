# FactsLens: Aiven "Best Use of Aiven"

*Draft.*

**Live:** https://srv2159312.hstgr.cloud/ (the app runs on Hostinger; Aiven runs its data layer).

FactsLens is a live radar for coordinated propaganda on Bluesky, Telegram and Mastodon. It uses two Aiven services, each for what it does best.

## Aiven for Apache Kafka: the backbone of the stream

**What it carries:** one topic, `posts` (1 partition for global time order, 2 replicas, 24-hour retention and a 2 GiB cap), connected with SASL/SCRAM over TLS. Three independent sources write into it:
- the whole public Bluesky stream, about 4,000 posts and reposts a minute;
- 40 Telegram channels;
- 78 Mastodon servers.

The detector consumes it.

**What it enables:**
- **Sources and detection are decoupled.** We added Telegram and Mastodon on Saturday as new producers to the same topic. The detector never changed how it reads.
- **Restarts lose nothing.** Every deploy restarts all services, and the detector resumes from its Kafka offset; the ingest resumes from its own cursor. In the first test, a killed and restarted ingest left no gap (largest pause 0.17 s).
- **Bursts are absorbed.** The public status page shows the consumer lag ("events waiting in Kafka") live.
- **Slow work stays off the ingest path.** Nemotron investigations take a minute each and never block reading the stream.
- **Cross-platform matching.** Because all platforms land in one time-ordered stream, the detector can drop the second copy of a post seen on two platforms, and spot content that appeared on Telegram first.

## Aiven for PostgreSQL: the state every page and report needs

**What it holds:** PostgreSQL 18.7 with 15 tables, created by our own migrations on start. They cover:
- candidate clusters, their accounts (as keyed hashes) and sample posts;
- profiles;
- Nemotron's reports and every model call (latency, tokens, retries, errors);
- campaign reports, contested stories, picture hashes;
- human reviews per cluster and per account;
- evaluation runs.

**What it enables:**
- **Seven services share one source of truth.** The detector writes clusters; enrich scores them; the investigator takes the highest-scoring uninvestigated cluster as a queue (SQL ordering); the website reads it all.
- **Explainability is stored, not recomputed.** Every signal's value, threshold and explanation, and the map from each evidence id in a Nemotron report to the post, image or page it stands for, are kept.
- **The feedback loop runs through it.** Reviewer decisions become the precision numbers on the metrics page and calibration examples in Nemotron's prompt.
- **Privacy is enforced in the database.** An hourly job removes raw ids, names, post texts and reports after 72 hours. Hashes and aggregate statistics stay.
- **SQL does real detection work.** Perceptual-hash matching for recycled pictures uses `bit_count` on the XOR of 64-bit hashes; campaign grouping joins clusters on shared accounts.

## Cost and region

- **Cost:** checked by the team on Saturday: an estimated **0.11 of the $50 credit per month**.
- **Region: EU.** Kafka runs on DigitalOcean in Amsterdam and PostgreSQL in Frankfurt (looked up from DigitalOcean's published IP location list, 14 Nov). With the Hostinger VPS in Düsseldorf, all data stays in the EU (GDPR).
- **Not used, honestly:** pgvector is available but not used; ClickHouse and Valkey were not needed.
