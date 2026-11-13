# Decision log

Newest first. Each entry: what we decided, why, and who decided. Proposals waiting for approval live in [`PLAN.md`](PLAN.md) §2, not here.

## 2026-11-14: M1 started; sources are pluggable; Telegram and Pravda added
- **Decision:** start M1 with D1–D9 as written in PLAN.md §2. Every data source implements one `Source` interface and produces the same `Event` record, so platforms plug into one pipeline. Bluesky is the live stream; **Telegram channel pages and the Pravda sites are added as upstream sources** (to trace laundered content back to where it came from).
- **Other platforms (checked 14 Nov, details in CLAUDE.md §9):** X is pay-per-use ($0.005 per post read), Reddit blocks unauthenticated access, Instagram has no official way to read arbitrary public posts, YouTube's API allows 100 searches/day. None are added now. No scraping around platform blocks.
- **Recording:** a local recorder saves the stream to `data/recordings/` (git-ignored) because Jetstream only replays about 36 hours. Recordings and the 24 h Kafka topic hold raw account IDs; keyed hashing applies to what we keep in PostgreSQL. Delete the recordings after the event.
- **Deploy detail (D1):** `infra/deploy.sh` will copy the committed tree over SSH and build on the VPS, so the VPS needs no GitHub credentials.
- **Decided by:** team (Sebastiaan); the recording and deploy details are Claude's, flagged here for review.

## 2026-11-13: No `.env` template in the repo
- **Decision:** each person creates their own `.env`; the repo has no `.env.example`, and the README doesn't explain the settings.
- **Decided by:** team (Sebastiaan).

## 2026-11-13: Project name
- **Decision:** the project is called **FactsLens**, tagline *"See who's really behind the post."* The Python package is `factslens`.
- **Why the tagline:** it echoes Hostinger's "Trust the source" wording ("who is really behind what they're reading") and makes clear we detect coordination, not just fact-check claims.
- **Decided by:** team (Sebastiaan) chose the name; the tagline is Claude's suggestion, easy to swap.

## 2026-11-13: Milestone hand-off
- **Decision:** after each milestone Claude summarises what works, what doesn't and the next step, then continues without waiting. Anything that costs money or is hard to undo still needs the team's OK. M1 starts only after the team approves D1–D9 in PLAN.md §2.
- **Decided by:** team (Sebastiaan).

## 2026-11-13: Service stack
- **Decision:**
  - **Must-haves:** the Hostinger VPS (app runs there) and NVIDIA Build (Nemotron 3.5 Super VL).
  - **Kept for the Aiven prize and real work:** Aiven PostgreSQL + Kafka. Kafka is the first thing to cut if time gets tight; Postgres would then move to the VPS.
  - **Not used:** Hostinger AI Router (kept only as a possible backup), Verda (no GPU work left: the simulation runs on CPU, Nemotron is hosted), ClickHouse, Valkey, OpenSearch, Hermes/Oxylabs.
- **Decided by:** team (Sebastiaan).

## 2026-11-13: Nemotron gets four jobs; the demo shows it at every step
- **Decision:** Nemotron 3.5 Super VL does four jobs (PLAN.md §1):
  1. investigator agent (images, video, websites, domain age, techniques, provenance);
  2. false-positive judge (real crowd vs staged);
  3. red-team generator of synthetic test campaigns;
  4. audience modelling for the spread forecast.

  Plus a feedback loop from reviewer decisions into the prompts.
- **Why:** NVIDIA scores whether Nemotron is central and whether its output is measured and improved. Each job has its own number on the metrics page.
- **Consequences:**
  - Jobs 1–3 sit in P0 milestones (M3–M4).
  - Job 4 is P1 (M5), after the metrics are safe.
  - The forecast is calibrated on observed reposts and reported with its measured error.
- **Decided by:** team (Sebastiaan).

## 2026-11-13: Keep Aiven, limited to Kafka + PostgreSQL
- **Decision:** use Aiven for Kafka and PostgreSQL only. ClickHouse and Valkey are not planned.
- **Why:**
  - a separate prize (Best Use of Aiven);
  - frees VPS memory;
  - Kafka enables replay for evaluation and buffers slow Nemotron calls.
- **If time gets tight:** Aiven is the first thing to cut; the main challenge works without it.
- **Decided by:** team (Sebastiaan).
