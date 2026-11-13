# Decision log

Newest first. Each entry: what we decided, why, and who decided. Proposals waiting for approval live in [`PLAN.md`](PLAN.md) §2, not here.

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
