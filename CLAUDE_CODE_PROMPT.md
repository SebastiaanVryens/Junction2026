# Project brief for Claude Code: SwarmScope (working name)

You are the lead engineer on our Junction 2026 hackathon team (Espoo, 13–15 Nov 2026). We have roughly 40 hours. Build the project below with us, milestone by milestone. Write structured, typed, tested, production-style code. Ask before anything that costs money, creates paid cloud services, or is hard to undo.

---

## 1. What we are building

A live radar for influence operations on Bluesky. The system:

1. **Ingests** every public Bluesky post in real time.
2. **Detects swarms:** groups of accounts acting together (timing, account similarity, text/image reuse, amplification patterns), not single posts.
3. **Investigates** each suspicious cluster with **NVIDIA Nemotron 3.5 Super VL**. It looks at the images and video, reads the linked websites, names the manipulation techniques in plain language, scores where the content comes from, and writes an evidence report for a human reviewer.
4. **Forecasts and simulates** (P1): how far the narrative will spread, and how much labeling specific accounts would reduce that.
5. **Detects recycled footage** (P2): old video or images re-captioned as new.
6. **Measures itself honestly:** precision and false-positive rate on a public metrics page.

One-line pitch: *"Other tools tell you a campaign happened. Ours shows who's behind it and how they operate, forecasts how far it will spread, and tells moderators which accounts to act on first."*

Audience: platform moderators, fact-checkers, newsrooms. The system **never accuses anyone automatically**. Every output is a report for a human to review.

---

## 2. The three challenges we are judged on (design for these criteria)

### A. Hostinger: "Decode the Noise" (main challenge, €2,000 / €1,000 / €500)
- **Hard requirement:** deployed and running live on Hostinger's infrastructure by the end of the hackathon, with a public URL the judges can open.
- **Judging:** detection quality (accuracy, false-positive handling, explainability) **40%** · technical execution & live deployment on Hostinger **25%** · originality & real-world impact **20%** · demo clarity **15%**.
- They care more about **strong, honest detection logic and false-positive handling** than a polished front end. They explicitly want **precision and false-positive rate shown**, not just a happy-path demo.
- We combine three of their four suggested directions:
  - **Spot the swarm:** coordination from timing, account similarity, text reuse, amplification (core).
  - **Name the technique:** classify techniques (loaded language, fear appeals, whataboutism, fabricated authority, …) and explain *why* in plain language.
  - **Trust the source:** provenance of linked domains and accounts (domain age, cloned content, AI-generated text, link networks).
- **What we get:** Hostinger VPS KVM 2 (2 vCPU, 8 GB RAM, 100 GB NVMe, root, Docker, free domain), Hostinger AI Router credits (150+ models, OpenAI/Anthropic-SDK compatible), Hostinger Connector / API / CLI, and an open-source MCP server (github.com/hostinger/api-mcp-server, docs: docs.hostinger.com/hostinger-connector, API: developers.hostinger.com).
- ⚠️ **The VPS has no GPU and only 8 GB RAM.** The core product must run on it. GPU work is an optional accelerator (see §4).

### B. NVIDIA: "Best Use of Nemotron" (2 × RTX 5080)
- **Must** use **Nemotron 3.5 Super VL** in the working solution, and Nemotron must be central and *visible* in the demo.
- **Judging (25% each):** meaningful Nemotron usage · technical execution (architecture, data flow, tool use, latency, error handling) · quality of AI output (grounded; improved through prompting, evaluation or feedback loops) · creativity and differentiation.
- **Required submission text:** model used, what it does, why it matters, **how we improved its outputs**. Keep `docs/IMPROVEMENTS.md` updated from the start so this text writes itself.
- **API:** OpenAI-compatible, base URL `https://integrate.api.nvidia.com/v1`. Look up the exact model ID for Super VL in the NVIDIA Build catalog; don't guess it. The free tier is about 40 requests/min per key, so implement retries with exponential backoff and rotate between teammates' keys. We also have a Verda API key (from the Junction Discord); support it as a second provider behind the same client interface.
- Use the model's multimodal abilities for real: images, video frames, linked web pages, tool calls.

### C. Aiven: "Best Use of Aiven" (€1,000 / €750 / €500)
- **Judging:** meaningful use of Aiven technology **40%** · technical implementation **30%** · impact on the overall solution **20%** · creativity **10%**.
- We get **$50 in free credits.** ⚠️ Before creating any service, estimate its cost for ~48 hours and tell us. Prefer free or startup plans. Plan to delete services after the event.
- Planned services, each with a real job:
  - **Aiven for Apache Kafka:** the backbone. Firehose → Kafka topics → detector, investigator and simulator consumers. It survives worker crashes and absorbs bursts.
  - **Aiven for PostgreSQL:** state (clusters, hashed account IDs, evidence, Nemotron reports, human review decisions, eval results). Use pgvector for similarity search if it's available on our plan.
  - **Aiven for ClickHouse** (if the budget allows): time-series of narrative volume and burst metrics, plus observability for the agent (latency, tokens, errors per Nemotron call). Aiven explicitly suggests "observe your agent with Aiven ClickHouse".
  - **Aiven for Valkey** (optional): cache and dedupe for the investigator and rate-limit state for API keys.
- **Do not** host the app on Aiven Runtime. The product must run on Hostinger.
- Choose an **EU region** for all services (GDPR).

---

## 3. Data sources (verify each endpoint before relying on it)

- **Bluesky Jetstream** (public firehose as JSON over WebSocket, no auth), e.g. `wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post&wantedCollections=app.bsky.feed.repost&wantedCollections=app.bsky.feed.like&wantedCollections=app.bsky.graph.follow`. Support reconnecting with cursor resume.
- **Images:** Bluesky CDN, built from the author's DID and the blob CID in the post record.
- **Video:** Bluesky video playlists (HLS). Extract a few keyframes with ffmpeg for Nemotron.
- **Follower graph** (for the P1 simulator): public AppView API (`https://public.api.bsky.app/xrpc/app.bsky.graph.getFollowers` and related). Respect rate limits and sample 1–2 hops around a cluster.
- **Linked websites** (provenance): fetch pages ourselves, plus RDAP/WHOIS for domain age. Compare against known propaganda-network domain lists.
- **Ground truth for evaluation:**
  - Documented campaigns: CheckFirst's July 2026 "Roska Bridge" report (Pravda-network accounts bridged from Mastodon to Bluesky), Alliance4Europe's report on the "Doppelgänger" operation on Bluesky, and IFTAS's alert on the coordinated pro-Russian network.
  - **Seeded synthetic campaigns** (see §6).
  - **Organic controls:** real breaking-news moments where many people legitimately post similar content. These are our main false-positive trap.

---

## 4. Architecture

```
Bluesky Jetstream ──> ingest (Hostinger VPS) ──> Aiven Kafka
                                                    │
        ┌───────────────────────────────────────────┼─────────────────────────┐
        ▼                                           ▼                         ▼
 detector (VPS, CPU)                    investigator (VPS)          GPU worker (optional, Verda/team GPU)
 - SimHash/MinHash text near-dups       - Nemotron 3.5 Super VL      - text/image embeddings (semantic clusters)
 - perceptual hashes for images         - tools: fetch page, RDAP,   - Monte Carlo spread simulation
 - burst detection (time windows)         keyframes, cluster stats   - writes results back to Kafka/Postgres
 - account features (age, bio sim,      - writes evidence report
   cadence, creation bursts)
 - coordination graph + scoring
        │                                           │                         │
        └──────────────> Aiven PostgreSQL (+ ClickHouse metrics) <────────────┘
                                         │
                         API (FastAPI) + web UI on Hostinger VPS (public URL)
                         - live narrative map  - cluster report  - review queue
                         - forecast / what-if slider  - metrics page (precision, FPR)
```

Design rules:
- **Graceful degradation:** the product must work end to end on the VPS alone using CPU-friendly methods (hashing, MinHash, small simulations). The GPU worker only adds semantic clustering and large simulations. If it's offline, the UI says so and carries on.
- **Keep VPS memory in check:** sliding windows, bounded queues, aggressive retention.
- **Backpressure:** never let Nemotron calls block ingest. The investigator consumes a priority queue of clusters, scored by risk times growth.
- Everything runs with Docker Compose. One command brings up the VPS stack. Configuration lives in `.env`, never committed.

---

## 5. Features and priorities

### P0: must ship (Hostinger 40% lives here)
1. Ingest → Kafka with reconnect/resume.
2. Swarm detection with **explainable signals**: every cluster shows *which* signals fired (timing synchrony, near-duplicate text/images, account creation burst, bio similarity, amplification ring) with values and thresholds.
3. Nemotron investigation report per cluster:
   - Summary, evidence list (each claim links to posts, images or pages), and **technique labels in plain language** (Hostinger's list), plus DISARM technique IDs where they apply.
   - **Provenance score** for linked domains (age, cloned content, AI-text signals, link network).
   - **Abstain option:** the verdict can be "insufficient evidence". Never force a label.
   - Structured JSON output validated with Pydantic. Retry or repair on schema failure.
4. Review UI: confirm / false alarm / unsure, stored in Postgres. Those decisions feed the metrics.
5. **Public metrics page:** precision, recall, false-positive rate per threshold, on seeded campaigns, documented accounts and organic controls, with sample sizes shown.
6. Live deployment on Hostinger with a domain.

### P1: what makes us unique
7. **Spread forecast + intervention simulator:** sample the follower graph around a cluster, run Monte Carlo spread simulations (independent-cascade model or similar), and show the expected reach with uncertainty. A "label these accounts" control re-runs the simulation and shows reach reduction, ranking which accounts to act on first. Back-test on past clusters (predict, then compare with actual spread) and show the error.

### P2: only if P0 and P1 are solid
8. **Recycled footage detector:** keyframes → perceptual hashes and embeddings → match against our archive and earlier copies on the web. Nemotron describes the scene and explains the mismatch ("first seen in 2022, different country, new caption").

---

## 6. Evaluation (this decides 40% of Hostinger's score and 25% of NVIDIA's)

- **Seeded synthetic campaigns:** a replay tool that injects synthetic coordinated posts (variable size, timing jitter, paraphrasing, image variants) into the pipeline's Kafka topic, marked so they never leave our system. Measures recall and how hard evasion is.
- **Documented real accounts** from the reports in §3: does the detector flag them, and how fast?
- **Organic controls:** breaking-news bursts and fandom events. Measures the false-positive rate. Explain which signals separate organic from inauthentic coordination (account age, creation bursts, cadence regularity, cross-cluster reuse).
- **Nemotron report quality:** a small rubric (grounded claims, correct technique, useful explanation) scored on a fixed set of clusters before and after each prompt or pipeline change. Log every change in `docs/IMPROVEMENTS.md`, along with the score difference.
- Write it all up in `docs/EVAL.md` and show it on the metrics page.

---

## 7. Ethics, privacy, safety (non-negotiable)

- No automatic public labeling or accusation. Reports are for human review only.
- GDPR: store hashed DIDs at rest, 72-hour retention for raw posts, EU regions, minimal fields. Show content only when the reviewer needs it.
- Synthetic campaigns are never posted to Bluesky. They exist only inside our Kafka topics.
- Never commit API keys. Use `.env` and add a `.env.example`.

---

## 8. Engineering standards

- Python 3.12, type hints everywhere, Pydantic models for all messages and LLM outputs, FastAPI for the API. Use whatever frontend gets a clear map and review UI fastest (keep it simple; Hostinger cares about detection, not polish).
- Small services with clear interfaces: `ingest/`, `detector/`, `investigator/`, `simulator/`, `api/`, `web/`, `eval/`, `infra/`.
- Unit tests for the detection signals and scoring. Integration test using the replay tool.
- Structured logging. Every Nemotron call logs latency, tokens, retries and errors (to ClickHouse if we have it).
- Use the Hostinger MCP server or CLI to deploy, if available in this session.

---

## 9. Deliverables

- Live public URL on Hostinger.
- `README.md` (what, why, how to run), `docs/ARCHITECTURE.md`, `docs/EVAL.md`, `docs/IMPROVEMENTS.md`.
- Draft submission texts in `docs/submissions/`:
  - `hostinger.md`: detection logic, false-positive handling, metrics, live URL.
  - `nvidia.md`: model used, what it does, why it matters, how we improved its outputs.
  - `aiven.md`: which services, what each enables, and why they matter to the solution.
- A 2–3 minute demo script: live map → click a cluster → Nemotron report → forecast slider → metrics page.

---

## 10. How to work with us

1. Start by proposing the repo structure and a milestone plan for the timeline below. Wait for our OK.
2. Then build in order: **M1** ingest → Kafka → detector skeleton on the VPS · **M2** swarm signals + Postgres + basic UI · **M3** Nemotron investigator + review UI · **M4** evaluation + metrics page · **M5** simulator (P1) · **M6** recycled footage (P2, optional) · **M7** final deploy, docs, submission texts.
3. After each milestone: summarise what works, what doesn't, and the next step. Keep `docs/IMPROVEMENTS.md` and the decision log current.
4. Flag risks early, especially VPS memory limits, API rate limits, Aiven credit burn, and anything that threatens the live deployment.

**Timeline:** Fri night: M1 · Sat morning: M2 · Sat afternoon: M3 + M5 · Sat night: M4 (+ M6 if on track) · Sun morning: M7, rehearse the demo. Confirm the exact submission deadline with us.
