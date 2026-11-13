# Build plan (v2, Fri 13 Nov ~23:45)

Project: **FactsLens**, *"see who's really behind the post."* Brief: [`CLAUDE.md`](../CLAUDE.md). Decision log: [`DECISIONS.md`](DECISIONS.md).
**How to approve:** reply "OK" or "change D3 to …". The decisions in §2 need an OK before M1 starts.

**Changed since v1:**
- Nemotron now has **four jobs** (§1), and the demo shows each one (§6).
- The forecast is calibrated on observed data.
- Video stays in.
- Likes/follows are collected only for suspicious accounts.
- The `clusters` Kafka topic is gone (Postgres is enough).
- M6 is dropped unless we're ahead.

---

## 0. Where we are: M0 access check

| Integration | Status |
|---|---|
| Bluesky Jetstream (firehose + cursor resume), AppView | ✅ |
| Nemotron 3.5 Super VL (`nvidia/nemotron-3.5-super-vl-120b-a12b`), text + image | ✅ |
| Aiven Kafka (SASL, produce + consume) | ✅ |
| Aiven PostgreSQL 18.7 + pgvector 0.8.7 | ✅ |
| Hostinger plugin (MCP) | ✅ |
| VPS | ⏳ KVM 2, Düsseldorf (EU), IP `179.67.199.107`, state "creating", no OS installed yet. Template should be **Ubuntu 24.04 with Docker** + Docker manager. |
| VPS SSH/Docker, public URL | ⏸ waiting for the VPS install |

**Measured on 13 Nov:**
- Firehose: ~104 post + repost events/s, ~520 B each after trimming (~4.7 GB/day uncompressed).
- Posts with media/links: 15% images, 2% video, 22% links.
- Likes: ~320/s.
- Nemotron latency: 0.7–17 s per small call.
- Account age: available from AppView profiles (`createdAt`) and the PLC directory.

---

## 1. Nemotron's four jobs (the spine of the product and the demo)

| # | Job | What Nemotron does | Milestone | How we measure it (metrics page) |
|---|---|---|---|---|
| **1** | **Investigator agent** | Agent loop with tools per suspicious cluster: **looks at images and video frames**, **reads linked websites**, **looks up domain registration age**, pulls more cluster posts when needed. Writes the evidence report: technique labels (Hostinger's four + DISARM where they apply), provenance score, evidence links, verdict or "insufficient evidence". | M3 | Rubric score per report (grounded claims, correct technique, useful explanation), per prompt version in `IMPROVEMENTS.md`. |
| **2** | **False-positive judge** | Before a cluster reaches a moderator: *real crowd or staged?* Weighs evidence like varied wording, old accounts and a real triggering event vs. identical wording, new accounts and synchronised timing. Same call as job 1 where possible. | M3 (built), M4 (measured) | **False-positive rate with vs. without the judge**, on the organic-control set. |
| **3** | **Red team: simulates the propaganda** | Generates synthetic test campaigns:<br>- fictional narratives<br>- many account personas with different styles<br>- rewording, several languages<br>- deliberate **evasion tactics** aimed at our detector<br><br>Injected into our own Kafka topic only, marked synthetic, never posted. | M4 | **Recall per difficulty level** (simple / varied / evasive), and how recall changes after each detector fix. |
| **4** | **Audience modelling for the forecast** | Reads samples of each audience community around a cluster (bios, recent posts, images) and estimates how strongly the narrative will resonate with each. Those estimates set the simulation's per-community parameters. | M5 | **Forecast error with vs. without Nemotron's input** (forecast at T, compare with what happened by T+1 h). |

**Feedback loop** (M4): moderator decisions (confirm / false alarm) become examples in the prompts for jobs 1 and 2. NVIDIA's judging names "feedback loops" explicitly.

**Verify first (start of M3):**
- Does Super VL support **tool calling** on NVIDIA Build? If not, gather the evidence first and use one big multimodal call.
- Does it accept **video input**? If not, send ffmpeg keyframes.

Both get added to `infra/check_access.py`.

**Honest cost:**
- Job 1 is the brief as written.
- Job 3 mostly replaces the hand-written test-campaign generator we needed anyway: about +1–2 h.
- Job 2 measurement is about +2–3 h in M4.
- Job 4 is about +4–6 h on top of the simulator.

These are rough estimates. Jobs 1–3 sit in P0 milestones; job 4 is P1, after the metrics are safe.

---

## 2. Decisions to approve

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 | **Deploy path** | `git pull` + `docker compose up -d --build` on the VPS over SSH, via `infra/deploy.sh`. Use the Hostinger MCP for VM metrics, firewall, DNS and domain. | Hostinger's Docker manager pulls a GitHub repo's compose file from the **`master`** branch (per its API description); ours is `main`, and it can't build from source without a registry. |
| D2 | **HTTPS** | Caddy in the compose stack (automatic Let's Encrypt). | One config file, no manual certs. |
| D3 | **Where data lives** | Kafka: trimmed stream, ~24 h retention + size cap. PostgreSQL: only posts/accounts in candidate clusters, plus clusters, reports, reviews, eval results. | ~4.7 GB/day won't fit a cheap Postgres plan. GDPR minimisation (CLAUDE.md §7). |
| D4 | **Two-stage detection** | Stage 1 on every post: near-duplicate text, shared links/domains, timing synchrony, repost amplification. Stage 2 for candidates only: account age and creation bursts, bio similarity, likes/follows for those accounts, then Nemotron. Start with these 4 + account age; add more signals if time allows. | Enriching everything is too expensive on 2 vCPU; these signals best separate organic from staged crowds. |
| D5 | **Code layout** | One Python package `factslens`, a sub-package per service, one Docker image with a different entry point per compose service. | Shared Pydantic models, one build; keeps CLAUDE.md §8's service boundaries. |
| D6 | **Frontend** | FastAPI + Jinja2 + HTMX, with Cytoscape.js (CDN) for the account-network "map". No Node build. | Fastest clear UI; Hostinger scores detection, not polish. |
| D7 | **Kafka client** | `kafka-python` (verified against our Aiven service; Aiven's documented library). | No new unknowns at ~100 msg/s. |
| D8 | **Investigator queue** | Priority queue in PostgreSQL (`… FOR UPDATE SKIP LOCKED`). The detector writes clusters straight to Postgres; no `clusters` topic. Priority = risk × growth. | Kafka isn't a priority queue; keeps Nemotron off the ingest path. |
| D9 | **Replay** | One replay tool serves both evaluation (fixtures, red-team campaigns) and the demo fallback if the firehose is quiet. | One tool, two uses. |

---

## 3. Repo structure

```
pyproject.toml              uv project, Python 3.12
docker-compose.yml          ingest, detector, investigator, simulator, api, caddy
Dockerfile                  one image for all Python services
Caddyfile
src/factslens/
  common/                   settings, Pydantic message models, kafka, db, logging, HMAC DIDs
  ingest/                   Jetstream clients (all posts + filtered likes/follows), cursor store, trim → Kafka
  detector/                 stage-1 signals, cluster builder, scoring, stage-2 enrichment
  investigator/             Nemotron client (providers, retries, keys), tools, jobs 1–2 prompts + report schema
  simulator/                follower-graph sampling, communities, calibration, Monte Carlo, job 4
  api/                      FastAPI routes, Jinja2 templates, static (HTMX, Cytoscape)
  eval/                     job 3 red-team generator, fixture recorder, replay runner, metrics, rubric
migrations/                 SQL files, applied in order on startup
tests/                      unit tests per signal + scoring; integration test via replay
eval/ground_truth/          report links + account lists (team provides)
eval/fixtures/              recorded organic-control windows (JSONL)
infra/                      check_access.py, deploy.sh
docs/                       PLAN.md, DECISIONS.md, ARCHITECTURE.md, EVAL.md, IMPROVEMENTS.md, submissions/
```

---

## 4. What we need from Aiven (team creates in the console)

| Topic | Contents | Partitions | Retention |
|---|---|---|---|
| `posts` | Trimmed posts + reposts. Synthetic events carry `synthetic: true`. | 1 (global time order for the window detector) | 24 h + size cap |
| `engagement` | Likes/follows for accounts in candidate clusters (second Jetstream connection, filtered by account) | 1 | 24 h |
| `access-check` | Exists (M0) | 1 | default |

- **Kafka:** please tell us the plan's **disk size**, so the size caps can be set.
- **PostgreSQL:** nothing to do; we enable `vector` and create tables from `migrations/`.

---

## 5. Milestones ("done when" = the check before moving on)

Each milestone ends with a redeploy and a short summary (CLAUDE.md §11).

### M1: ingest → Kafka → detector skeleton (Fri night)
**Build:**
- Settings and message models.
- Jetstream client with reconnect and cursor in Postgres.
- Trim → `posts`.
- Detector consuming `posts` with sliding-window counters.
- `api`: `/health` and a status page (ingest rate, last event, consumer lag).
- Compose stack with Caddy and `infra/deploy.sh`.

**Done when:** a restart resumes from the cursor with no gap, and the public URL shows a live ingest rate (once the VPS is ready).

### M2: swarm signals + Postgres + basic UI (Sat morning)
**Build:**
- Stage-1 signals (D4).
- Cluster builder: account graph with shared-signal edges.
- Scoring, with each signal's value and threshold stored.
- Stage-2 enrichment: profiles/account age, filtered likes/follows.
- UI: cluster list + cluster page with a "signals fired" table and the network graph.
- Unit tests per signal and for scoring.

**Done when:** live clusters show which signals fired, with values and thresholds.

### M3: Nemotron jobs 1 + 2 + review UI (Sat afternoon)
**Build:**
- Verify tool calling + video input first.
- Investigator agent (job 1) and real-vs-staged verdict (job 2).
- Pydantic-validated output with repair/retry; abstain.
- Per-call logging (latency, tokens, retries, errors).
- Review UI: confirm / false alarm / unsure.

**Done when:** a cluster page shows a grounded Nemotron report with images/video/website evidence and a verdict, and a reviewer decision is stored.

### M4: evaluation + metrics page (Sat afternoon/evening, P0: must not slip)
**Inputs:**
- Job 3 red-team campaigns → `posts`, at three difficulty levels.
- Organic-control recorder: real breaking-news/fandom windows → `eval/fixtures/`.
- Ground-truth backfill via `getAuthorFeed` (needs `eval/ground_truth/`); report how many accounts are gone.

**Replay runner:**
- Detector at many thresholds.
- Job 2 judge on vs. off.

**Feedback loop:** reviewer decisions → prompt examples.

**Metrics page:**
- Precision, recall and FPR per threshold, with sample sizes and units (cluster vs account).
- Recall per red-team difficulty.
- FPR with vs. without the Nemotron judge.
- Rubric scores per prompt version.

**Done when:** all of that is public, and `docs/EVAL.md` explains it.

### M5: forecast with Nemotron audience modelling (Sat night, P1)
**Build:**
- Sample 1–2 hops of the follower graph and split it into communities.
- Calibrate reposting rates from what we observed in the stream.
- Job 4 resonance per community.
- Monte Carlo reach with uncertainty.
- "Label these accounts" control that ranks accounts by reach reduction.

**Done when:** the cluster page shows the forecast + slider, and the metrics page shows forecast error with vs. without job 4.

### M6: recycled footage. Dropped unless M1–M5 are done.

### M7: final deploy, docs, submissions, demo (Sun morning)
**Build:**
- Deploy freeze (time TBD from the deadline).
- `README.md`, `docs/ARCHITECTURE.md`, `docs/submissions/{hostinger,nvidia,aiven}.md`.
- Demo rehearsal (§6), with replay as backup.

---

## 6. Demo (2–3 min): Nemotron at every step

1. **Live map:** clusters appearing on the Bluesky stream (Kafka on Aiven, app on Hostinger).
2. **Click a cluster:** which signals fired, with values and thresholds.
3. **Nemotron investigation (job 1):** it looked at the images and video, read the linked sites, checked domain age, named the techniques, linked every claim to evidence.
4. **Real or staged? (job 2):** Nemotron's verdict with reasons, or "insufficient evidence".
5. **Forecast (job 4):** expected reach with uncertainty, shaped by Nemotron's audience reading; the "label these accounts" slider.
6. **Metrics page:**
   - precision / FPR, with "Nemotron's judge cut false alarms by X%";
   - recall against Nemotron's red-team campaigns (job 3);
   - forecast error with vs. without Nemotron.

---

## 7. Parallel workstreams (team size TBD: assign names once known)

| Stream | Owns | Starts |
|---|---|---|
| A: Pipeline | ingest, detector signals, scoring, enrichment | M1 |
| B: Nemotron | jobs 1–4, prompts, tools, rubric, feedback loop, `IMPROVEMENTS.md` | M1 (prompt/schema work needs no pipeline) |
| C: Product | API, UI, review flow, metrics page | M1 (against fixture data) |
| D: Eval + infra | VPS/deploy, replay runner, organic fixtures, ground truth, `EVAL.md` | M1 |

---

## 8. Budgets (estimates; measured values replace them)

**VPS memory (8 GB):**
- detector ~1.5 GB
- simulator ~1 GB while running
- ingest, investigator and API ~0.3 GB each
- Caddy ~50 MB

**Nemotron (~40 requests/min per key):**
- Jobs 1+2: ~5–8 calls per cluster, targeting ≤ 1 cluster/min.
- Job 3: offline batches, ~100 calls per test set.
- Job 4: ~5–10 calls per forecast, top clusters only.
- Cache by cluster fingerprint.

**Aiven:** cheapest plans; check credit usage Saturday morning and evening.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| VPS not ready yet | Develop locally with the same compose stack; deploy as soon as it's installed. |
| Super VL has no tool calling on NVIDIA Build | Job 1 falls back to gathering evidence first and one big multimodal call. |
| Nemotron declines to write red-team campaigns | Fictional narratives, internal research framing, documented tactics rather than real claims. |
| Job 4 doesn't improve the forecast | Report it honestly; the with/without comparison is the result either way. |
| Nemotron slow or rate-limited | Queue, backoff, cache, timeouts that end in "insufficient evidence". |
| AppView rate limits (unknown) | Batch, cache, back off, enrich candidates only. |
| False positives on organic bursts | Organic-control set, account-age signals, job 2 judge, abstain. |
| Kafka disk fills | Trimmed events, time + size retention caps, consumer-lag panel. |
| Quiet firehose during judging | Replay (D9). |
| Hosted Nemotron/Aiven questioned as "not on Hostinger" | Ask the Hostinger mentors; the app itself runs only on the VPS. |

---

## 10. Open items and when we need them

| Item | Needed by |
|---|---|
| Submission deadline | **Now** (sets the M7 freeze) |
| VPS installed with Ubuntu 24.04 + Docker; domain | M1 |
| Kafka plan disk size | M1 |
| Hostinger mentors confirm hosted Nemotron + Aiven is fine | Saturday morning |
| May the deployed app pool NVIDIA keys? | M3 |
| Propaganda-network domain lists | M3 |
| `eval/ground_truth/` links + account lists | Start of M4 |
