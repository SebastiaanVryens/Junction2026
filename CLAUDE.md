# Project brief for Claude Code: FactsLens

*FactsLens: see who's really behind the post.*

You are the lead engineer on our Junction 2026 hackathon team (Espoo, 13–15 Nov 2026). We have roughly 40 hours. Build the project below with us, milestone by milestone. Write structured, typed, tested, production-style code. Ask before anything that costs money, creates paid cloud services, or is hard to undo.

**Don't guess facts.** The challenge requirements in §2 are taken from the official Junction challenge pages. Anything we don't know yet is marked **TBD** and listed in §9. If you need a TBD item, stop and ask us. Don't fill it in yourself, and don't rely on your memory for model IDs, API parameters, report contents or deadlines.

---

## 1. What we are building

A live radar for influence operations on Bluesky. The system:

1. **Ingests** public Bluesky activity in real time (volume policy in §3).
2. **Detects swarms:** groups of accounts acting together (timing, account similarity, text/image reuse, amplification patterns), not single posts.
3. **Investigates** each suspicious cluster with **NVIDIA Nemotron 3.5 Super VL**. It looks at the images and video, reads the linked websites, names the manipulation techniques in plain language, scores where the content comes from, and writes an evidence report for a human reviewer. **Nemotron has four jobs, and the demo shows each one** (details and metrics in `docs/PLAN.md` §1):
   1. **Investigator agent:** images, video, linked websites, domain age → evidence report.
   2. **False-positive judge:** real crowd or staged?
   3. **Red team:** generates the synthetic test campaigns, including evasion tactics.
   4. **Audience modelling:** estimates how strongly a narrative resonates with each audience community; feeds the forecast.
4. **Forecasts and simulates** (P1): how far the narrative will spread, and how much labeling specific accounts would reduce that. Calibrated on reposts we observe in the stream, and shown as an estimate with its measured error.
5. **Detects recycled footage** (P2): old video or images re-captioned as new.
6. **Measures itself honestly:** precision and false-positive rate on a public metrics page.

One-line pitch: *"Other tools tell you a campaign happened. Ours shows who's behind it and how they operate, forecasts how far it will spread, and tells moderators which accounts to act on first."*

Audience: platform moderators, fact-checkers, newsrooms. The system **never accuses anyone automatically**. Every output is a report for a human to review.

---

## 2. The three challenges we are judged on (official requirements)

### A. Hostinger: "Decode the Noise" (main challenge)

**Official task:** "Build a tool that analyses text, social content or web-traffic patterns to detect signs of coordinated propaganda, bot-driven amplification or bad-actor behaviour online." Accepted forms: an API, a dashboard, a classifier with an explainable verdict, or a monitoring service for site owners.

**Hard requirement:** "it must be deployed and running live on Hostinger's infrastructure by the end of the hackathon, with a public URL the judges can open." Any language, framework or model, open-source or hosted, "as long as the final product runs on Hostinger."

**Judging:** detection quality (accuracy, false-positive handling, explainability) **40%** · technical execution & live deployment on Hostinger **25%** · originality & real-world impact **20%** · demo clarity **15%**.

**What they emphasise:** "the strength and honesty of your detection logic – including how you handle false positives" over a polished front end. "Approaches that are transparent and measurable – show your precision and false-positive rate, not just a demo that works on the happy path."

**Directions** (official: "pick one, or combine them"). We combine three of the four:
- **Spot the swarm** (core): detect coordinated inauthentic behaviour (bot networks, astroturfing, brigading) from posting timing, account similarity, text reuse and amplification patterns rather than from any single post.
- **Name the technique:** classify manipulation techniques in text and explain the verdict in plain language, "so a reader learns why something is propaganda, not just that it is." Hostinger's examples are exactly: **loaded language, fear appeals, whataboutism, fabricated authority**. Cover at least these four. We may add more (e.g. DISARM techniques), but list every added label in `docs/EVAL.md`.
- **Trust the source:** score the provenance of a page or account (domain age, cloned content, AI-generated text, link networks).
- *Not chosen:* "Clean the traffic" (web-server log bot detection).

**What Hostinger provides** (free for 1 month, per team):
- **VPS KVM 2:** 2 vCPU, 8 GB RAM, 100 GB NVMe, 8 TB bandwidth, full root access, Docker support, a free domain, and one-click templates for Ollama, n8n, Node.js and Python stacks.
- **AI Router credits:** one API key and **one shared credit balance** for 150+ models (Anthropic, OpenAI, DeepSeek, Mistral, xAI and more), OpenAI- and Anthropic-SDK compatible. It has no job in our design yet. Don't spend from it without asking us.
- **AI Builder** 1-month plan (front end / landing page builder).
- **Hostinger Connector, API and CLI:** connect Claude Code or any MCP client to deploy, manage the server and connect a domain. Docs: docs.hostinger.com/hostinger-connector · API: developers.hostinger.com · MCP server: github.com/hostinger/api-mcp-server. Setup and safety rules are in §8.
- Mentors at the Hostinger booth (deployment, scaling, model choice, detection strategy) and a Hostinger help channel.

⚠️ **The VPS has no GPU and only 8 GB RAM.** The core product must run on it. GPU work is an optional accelerator (see §4).

### B. NVIDIA: "Best Use of Nemotron"

**Official challenge:** "Build a project powered by NVIDIA Nemotron that solves a real problem through reasoning, tool use, or coordinated workflows." "Nemotron must contribute directly to the project's core functionality, and your demo should make that contribution visible."

**Minimum requirements (official, all mandatory):**
1. Use the **NVIDIA Nemotron 3.5 Super VL** model in the working solution.
2. Demonstrate its contribution to the project's core functionality.
3. Submit a short explanation identifying **the model used, what it does, why it matters, and how we improved its outputs**, as text input in the submission form. Keep `docs/IMPROVEMENTS.md` updated from the start so this text writes itself.

**About the model (per NVIDIA):** 120B parameters, about 12B active per token. Reads text, images, documents and video in one workflow, and can call tools.

**Judging (25% each):**
- **Meaningful Nemotron usage:** central to the project's value; we clearly explain what it does and why it matters.
- **Technical execution:** the demo works reliably; thoughtful choices on architecture, data flow, tool use, latency or error handling.
- **Quality of AI output:** useful, relevant and grounded; we show efforts to improve quality through prompting, evaluation or feedback loops.
- **Creativity and differentiation.**

**Prize:** two winning teams, 1× RTX 5080 each.

**Access (official options):**
- **NVIDIA Build** (free hosted API): OpenAI-compatible, base URL `https://integrate.api.nvidia.com/v1`. NVIDIA's example model ID `nvidia/nemotron-3.5-lightning-30b-a3b` is a *different* model, not Super VL. The NVIDIA Build catalog (`GET /v1/models`, checked 13 Nov 2026) lists Super VL as **`nvidia/nemotron-3.5-super-vl-120b-a12b`**. `infra/check_access.py nvidia` confirms it with a real call per key. If Super VL isn't available on any provider, **stop and tell us**. Never substitute another model, because Super VL is a minimum requirement.
- **Rate limits:** "developer forums report about 40 requests per minute per key". Implement retries with exponential backoff. NVIDIA says "each teammate should use their own key". Whether the deployed app may pool several teammates' keys is **TBD** (§9). Until then, the client accepts a list of keys but the default config uses one.
- **OpenRouter:** free variants (ending in `:free`) are capped at 20 requests/min and 50/day, or 1,000/day once the account has bought at least 10 credits. Buying credits costs money, so ask first. Check whether Super VL is listed there before relying on it.
- **Hugging Face:** open weights for local runs or deployment on Brev. Not on our VPS (no GPU, 8 GB RAM).
- **Verda:** we have a Verda API key from the Junction Discord. This is *not* in NVIDIA's official text, and what it provides (Nemotron API, or GPU compute) is **TBD** (§9).
- Put all providers behind one client interface so we can switch by config.

**Use the multimodal abilities for real:** images, video, linked web pages, tool calls. NVIDIA says the model reads video. Check whether the API accepts video input directly; if it doesn't, send ffmpeg keyframes.

### C. Aiven: "Best Use of Aiven"

**Official challenge:** "Use at least one Aiven service as a meaningful part of your Junction project, and demonstrate what it enables." "A well-executed use of one service can be just as impressive as a solution combining several." Services they name: Aiven Runtime, PostgreSQL, Valkey, Kafka, ClickHouse, OpenSearch.

**Judging:** meaningful use of Aiven technology **40%** · technical implementation **30%** · impact on the overall solution **20%** · creativity **10%**.

**Prizes:** €1,000 / €750 / €500. Aiven mentors are on site all days.

**Budget:** $50 in free credits on sign-up. ⚠️ Before creating any service, estimate its cost for ~48 hours and tell us. Prefer free or startup plans. Plan to delete services after the event. **We (the team) create and configure the Aiven services** in the Aiven console. You write code against them and tell us exactly what you need (topics, partitions, retention, extensions, plan size), with a cost estimate.

**Our plan, in priority order** (depth beats breadth, per Aiven's own guidance):
1. **Aiven for Apache Kafka:** the backbone. Firehose → Kafka topics → detector, investigator and simulator consumers. It survives worker crashes and absorbs bursts.
2. **Aiven for PostgreSQL:** state (clusters, keyed-hash account IDs, evidence, Nemotron reports, human review decisions, eval results). pgvector is available for similarity search (checked 13 Nov: PostgreSQL 18.7, pgvector 0.8.7, not yet enabled).
3. *Only if budget and time allow:* **Aiven for ClickHouse** for narrative-volume time series and agent observability (latency, tokens, errors per Nemotron call). Aiven's text names this use: "observe your agent with Aiven ClickHouse".
4. *Optional:* **Aiven for Valkey** for investigator cache/dedupe and API-key rate-limit state.

**Do not** host the app on Aiven Runtime. Hostinger requires the product to run on Hostinger. Choose an **EU region** for all services (GDPR).

---

## 3. Data sources (verify each endpoint before relying on it)

- **Bluesky Jetstream** (public firehose as JSON over WebSocket, no auth), e.g. `wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post&wantedCollections=app.bsky.feed.repost`. Support reconnecting with cursor resume.
- **Volume policy (VPS: 2 vCPU / 8 GB; Aiven: $50):** take all posts and reposts. Likes are far higher volume. Measured 13 Nov 2026 (Fri evening, 10 s sample): posts ~51/s, reposts ~50/s, likes ~311/s, follows ~32/s. Take them only for accounts in candidate clusters (e.g. a second Jetstream connection filtered by DID; check Jetstream's docs for the filter parameter and its limits), or sample them. Measure throughput on the VPS and Kafka before widening anything.
- **Images:** Bluesky CDN, built from the author's DID and the blob CID in the post record.
- **Video:** Bluesky video playlists (HLS). See §2B on video input vs ffmpeg keyframes.
- **Follower graph** (for the P1 simulator): public AppView API (`https://public.api.bsky.app/xrpc/app.bsky.graph.getFollowers` and related). Respect rate limits and sample 1–2 hops around a cluster.
- **Linked websites** (provenance): fetch pages ourselves, plus RDAP/WHOIS for domain age. Compare against known propaganda-network domain lists (sources **TBD**, §9).
- **Ground truth for evaluation:**
  - Documented campaigns: CheckFirst's July 2026 "Roska Bridge" report (Pravda-network accounts bridged from Mastodon to Bluesky), Alliance4Europe's report on the "Doppelgänger" operation on Bluesky, and IFTAS's alert on the coordinated pro-Russian network. **We will put the report links and account lists in `eval/ground_truth/`.** Use only those files, not your memory of the reports; they may postdate your training.
  - These accounts' old posts are outside Jetstream's replay window. Backfill them through the public AppView (e.g. `app.bsky.feed.getAuthorFeed`) and feed them through the replay tool. Expect some accounts to be suspended or deleted, and report how many.
  - **Measured 14 Nov:** a replay of 1 hour of live posts (205,007 posts, 37,962 with links) found **0 links** to any VIGINUM or CheckFirst domain. Live hits on known-bad domains are rare: use the lists for evaluation and as a high-confidence signal, not as the demo's source of hits. Detect *new* waves by their pattern: bridged `*.ap.brid.gy` accounts from IFTAS's listed instances, new accounts, synchronised bursts.
  - **Jetstream replay depth, measured 14 Nov: about 36.6 hours.** Anything older is gone, so a collector must be running to keep history. Same replay: 157 posts/hour carry Telegram links; 9 of them (4 accounts) link a channel that Pravda republishes from (7,648 such channels in CheckFirst's data; a lead, not proof). About 1% of posting accounts are bridged from Mastodon (2 of a random 200).
  - **Seeded synthetic campaigns** (see §6).
  - **Organic controls:** real breaking-news moments where many people legitimately post similar content. These are our main false-positive trap.

---

## 4. Architecture

```
Bluesky Jetstream ──> ingest (Hostinger VPS) ──> Aiven Kafka
                                                    │
        ┌───────────────────────────────────────────┼─────────────────────────┐
        ▼                                           ▼                         ▼
 detector (VPS, CPU)                    investigator (VPS)          GPU worker (optional; host TBD, §9)
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

### P0: must ship (Hostinger's 40% lives here)
1. Ingest → Kafka with reconnect/resume.
2. Swarm detection with **explainable signals**: every cluster shows *which* signals fired (timing synchrony, near-duplicate text/images, account creation burst, bio similarity, amplification ring) with values and thresholds.
3. Nemotron investigation report per cluster:
   - Summary, evidence list (each claim links to posts, images or pages), and **technique labels in plain language**: at least Hostinger's four (loaded language, fear appeals, whataboutism, fabricated authority), plus DISARM technique IDs where they apply.
   - **Provenance score** for linked domains (age, cloned content, AI-text signals, link network).
   - **Abstain option:** the verdict can be "insufficient evidence". Never force a label.
   - **Real crowd or staged?** (Nemotron job 2): a second opinion with reasons, e.g. people reacting to real news vs. a scripted push. Measure the false-positive rate with and without it.
   - Structured JSON output validated with Pydantic. Retry or repair on schema failure.
4. Review UI: confirm / false alarm / unsure, stored in Postgres. Those decisions feed the metrics.
5. **Public metrics page:** precision, recall, false-positive rate per threshold, on seeded campaigns, documented accounts and organic controls, with sample sizes shown.
6. Live deployment on Hostinger with a domain: **public URL up from M1**, redeployed after every milestone. Don't leave it for the end.

### P1: what makes us unique
7. **Spread forecast + intervention simulator:** sample the follower graph around a cluster, run Monte Carlo spread simulations (independent-cascade model or similar), and show the expected reach with uncertainty. A "label these accounts" control re-runs the simulation and shows reach reduction, ranking which accounts to act on first. Back-test on past clusters (predict, then compare with actual spread) and show the error. Calibrate reposting rates from what we observe in the stream. Nemotron job 4 estimates per-community resonance; report forecast error with and without it.

### P2: dropped unless P0 and P1 are fully done
8. **Recycled footage detector:** keyframes → perceptual hashes and embeddings → match against our archive and earlier copies on the web. Nemotron describes the scene and explains the mismatch ("first seen in 2022, different country, new caption").

---

## 6. Evaluation (Hostinger 40% "detection quality" + NVIDIA 25% "quality of AI output")

- **Unit of measurement:** state for every metric whether it is per cluster or per account, and how the ground-truth label was obtained. Never mix the two in one number.
- **Seeded synthetic campaigns:** a replay tool that injects synthetic coordinated posts (variable size, timing jitter, paraphrasing, image variants) into the pipeline's Kafka topic, marked so they never leave our system. Measures recall and how hard evasion is. **Nemotron generates them** (job 3): fictional narratives, varied account personas, several languages, deliberate evasion tactics. Report recall per difficulty level.
- **Documented real accounts** from `eval/ground_truth/` (§3): does the detector flag them, and how fast?
- **Organic controls:** breaking-news bursts and fandom events. Measures the false-positive rate. Explain which signals separate organic from inauthentic coordination (account age, creation bursts, cadence regularity, cross-cluster reuse).
- **Nemotron report quality:** a small rubric (grounded claims, correct technique, useful explanation) scored on a fixed set of clusters before and after each prompt or pipeline change. Log every change in `docs/IMPROVEMENTS.md`, along with the score difference. Reviewer decisions (confirm / false alarm) feed back into the prompts as examples (feedback loop).
- Write it all up in `docs/EVAL.md` and show it on the metrics page.

---

## 7. Ethics, privacy, safety (non-negotiable)

- No automatic public labeling or accusation. Reports are for human review only.
- GDPR: EU regions, minimal fields, 72-hour retention for raw posts. Show content only when the reviewer needs it.
- **Account IDs at rest:** use a keyed hash (HMAC, secret in `.env`). A plain hash of a public DID is reversible by hashing the firehose. Keep raw DIDs only for active clusters within the 72-hour window, because reviewers must see which accounts to act on and the simulator needs DIDs for `getFollowers`.
- Synthetic campaigns are never posted to Bluesky. They exist only inside our Kafka topics.
- Never commit API keys. Each person creates their own `.env`; the repo has no `.env` template (team decision), so don't add one.

---

## 8. Engineering standards

- Python 3.12, type hints everywhere, Pydantic models for all messages and LLM outputs, FastAPI for the API. Use whatever frontend gets a clear map and review UI fastest (keep it simple; Hostinger cares about detection, not polish).
- Small services with clear interfaces: `ingest/`, `detector/`, `investigator/`, `simulator/`, `api/`, `web/`, `eval/`, `infra/`.
- Unit tests for the detection signals and scoring. Integration test using the replay tool.
- Structured logging. Every Nemotron call logs latency, tokens, retries and errors (to ClickHouse if we have it).

### Deploying with the Hostinger MCP server
- **Setup** (from docs.hostinger.com/hostinger-connector/manual-configuration and the repo README):
  - Hosted server: `claude mcp add --transport http hostinger https://mcp.hostinger.com`. It signs in through the browser with OAuth; no token is needed.
  - Local server: npm package `@hostinger/mcp`. To load only the VPS tools: `npx -y --package=@hostinger/mcp hostinger-vps-mcp` (likewise `hostinger-dns-mcp`, `hostinger-domains-mcp`). If `HOSTINGER_API_TOKEN` is set, it is used instead of OAuth. Treat any config file containing it as a secret.
  - The docs and the README disagree on the minimum Node.js version (20 vs 24). Check before installing.
  - **We (the team) set up Hostinger:** the VPS, the domain, the API token and the Connector/MCP connection. If the MCP tools aren't available in your session, ask us; don't work around it.
- **Installed:** the official Claude Code plugin `hostinger@claude-plugins-official` (v1.2.0, user scope). It connects the hosted all-in-one server `https://mcp.hostinger.com` and signs in with OAuth.
  - It includes billing and purchase tools, so the never-without-asking list below is essential.
  - Its bundled skills (`hosting-deploy-*`, `agency-hosting-deploy-*`) deploy to shared hosting/WordPress, not our VPS. Don't use them.
- **Deploy path:** the VPS runs our Docker Compose stack.
  - Use the VPS tools: `vps_docker_create`, `vps_docker_update`, `vps_docker_logs`, `vps_docker_containers`.
  - Watch memory with `vps_virtual-machines_metrics`.
  - Note: `vps_docker_create` **replaces** an existing project with the same name.
  - Do **not** use `hosting_deploy`; that is for shared/cloud web hosting, not our VPS.
- **Never call these without our explicit OK, every time:**
  - Costs money: `vps_virtual-machines_purchase`, `billing_orders_create-purchase`, `domains_portfolio_purchase`, `billing_subscriptions_renew`.
  - Destroys data: `vps_virtual-machines_recreate` (wipes the VM), `vps_docker_delete` (removes volumes), `vps_backups_restore`, `vps_snapshots_restore`.
  - Overwrites or locks us out:
    - DNS: `dns_records_update` (can replace all records), `dns_records_reset`, `domains_portfolio_update-nameservers`.
    - Access: `vps_firewall_replace-all-rules-in-group`, `vps_firewall_delete`, `vps_ssh-keys_remove-virtual-machine`.
- Don't add permission allow-rules for Hostinger tools. Every call stays on manual approval.

---

## 9. Access and open items (team fills in; if one is still TBD when you need it, stop and ask)

Connection settings live in each person's own `.env`. `uv run infra/check_access.py` shows which ones work.

| Item | Value |
|---|---|
| Submission deadline (date, time, timezone) | TBD |
| Hostinger VPS, domain, API token, MCP connection | **Team sets these up** |
| VPS IP / hostname, SSH user | TBD (`VPS_HOST`, `VPS_SSH_USER` in `.env`) |
| VPS OS template (plain Docker, or a one-click template) | TBD |
| Domain name (Hostinger free domain) | TBD (`PUBLIC_URL` in `.env`) |
| Aiven services (Kafka, PostgreSQL, …) | **Team creates them** in the Aiven console |
| Nemotron 3.5 Super VL model ID | `nvidia/nemotron-3.5-super-vl-120b-a12b`: confirmed 13 Nov with real text and image calls (~11 s for a tiny text call) |
| Hostinger mentors confirm: app on the VPS calling hosted Nemotron (NVIDIA API) + Aiven Kafka/Postgres counts as "runs on Hostinger"? (Official text allows "hosted" models; confirm anyway) | TBD |
| Other platforms (team asked 14 Nov) | Checked 14 Nov:<br>- **X:** no free tier. Official docs: pay-per-use, **$0.005 per post read**, credits bought upfront, 3M reads/month cap; streaming not documented. Needs a developer account and the team's OK to spend.<br>- **Reddit:** every unauthenticated endpoint returns 403 from here. Third-party reports (unverified) say new free API sign-ups closed 31 Oct 2026. Usable only if a teammate already has API credentials.<br>- **Telegram** public channel pages (`t.me/s/<channel>`) and the Pravda sites are readable without login.<br>- Don't scrape around platform blocks. Decision on adding X: TBD. |
| May the deployed app pool several teammates' NVIDIA keys? (ask at the NVIDIA table) | TBD |
| Verda: what the key provides (Nemotron API? GPU compute?) | TBD |
| GPU worker host, if any | TBD |
| Propaganda-network domain lists for provenance | Verified 14 Nov:<br>- VIGINUM CSVs on GitHub (`VIGINUM-FR/Rapports-Techniques`): Portal Kombat/Pravda, 369 unique domains; RRN/Doppelgänger, 637 domains. Defanged as `[.]`; no licence file; TLP:CLEAR.<br>- CheckFirst `CheckFirstHQ/pravda-network`: `*.news-pravda.com` subdomains, updated 13 Nov.<br>- American Sunlight Project spreadsheet: page exists at americansunlight.org/portal-kombat-pravda-database; format not checked yet. |
| Ground-truth report links and account lists in `eval/ground_truth/` | Verified 14 Nov:<br>- IFTAS "Suspected Portal Kombat Accounts" (public Google Sheet, linked from about.iftas.org): ~2,850 Mastodon profile URLs. Bluesky copies follow Bridgy Fed's pattern `user.instance.ap.brid.gy`. Full lookup of all 2,850 on 14 Nov (`uv run eval/ground_truth/fetch.py --resolve-bluesky`, output in git-ignored `eval/ground_truth/data/`): **2,333 suspended by Bluesky**, 423 not on Bluesky, **94 still active** (57 have posts; 31 posted in the last 7 days, 6 in the last 24 h; 92 have <10 followers, 93 follow ≤1 account, 71 have no banner). IFTAS states a 0.33% false-positive rate (21 of 6,375). No licence stated: download at run time, don't commit.<br>- CheckFirst "Roska Bridge" PDF (July 2026), **CC BY-SA**. No account list on its web page; the PDF annexes are not yet read. |
| After each milestone: wait for our OK, or continue to the next? | **Summarise, then continue** (team, 13 Nov). Anything that costs money or is hard to undo still needs our OK. |

---

## 10. Deliverables

- Live public URL on Hostinger.
- `README.md` (what, why, how to run), `docs/ARCHITECTURE.md`, `docs/EVAL.md`, `docs/IMPROVEMENTS.md`, `docs/DECISIONS.md` (decision log).
- Draft submission texts in `docs/submissions/`:
  - `hostinger.md`: detection logic, false-positive handling, precision and false-positive rate, live URL.
  - `nvidia.md`: the model used, what it does, why it matters, how we improved its outputs (pasted as text into the submission form).
  - `aiven.md`: which services, what each enables, and why they matter to the solution.
- A 2–3 minute demo script: live map → click a cluster → Nemotron report → forecast slider → metrics page.

---

## 11. How to work with us

1. **M0 (first): access check.** `uv run infra/check_access.py` tests every integration:
   - Bluesky: Jetstream (with cursor replay) and the AppView.
   - NVIDIA: Super VL with every key, text and image.
   - Aiven: Kafka produce/consume, PostgreSQL + pgvector.
   - Hostinger: API token, VPS over SSH with Docker, the public domain.

   Fix or knowingly skip each FAIL before M1. Add a check whenever we add an integration.
2. Then propose the repo structure and a milestone plan for the timeline below. Wait for our OK. The current proposal is in [`docs/PLAN.md`](docs/PLAN.md); its §1 decisions need approval before M1.
3. Then build in this order (P0 before P1):
   - **M1:** ingest → Kafka → detector skeleton on the VPS, public URL live.
   - **M2:** swarm signals + Postgres + basic UI.
   - **M3:** Nemotron jobs 1 + 2 (investigator, false-positive judge) + review UI.
   - **M4:** evaluation + metrics page, with Nemotron job 3 (red-team campaigns) and the feedback loop.
   - **M5:** forecast simulator with Nemotron job 4 (audience modelling) (P1).
   - **M6:** recycled footage (P2, dropped unless everything else is done).
   - **M7:** final deploy, docs, submission texts.
4. After each milestone: redeploy, then summarise what works, what doesn't, and the next step. Keep `docs/IMPROVEMENTS.md` and `docs/DECISIONS.md` current.
5. Flag risks early, especially VPS memory limits, API rate limits, Aiven credit burn, and anything that threatens the live deployment.

**Timeline:**
- **Fri night:** M0 → M1
- **Sat morning:** M2
- **Sat afternoon:** M3 + M4
- **Sat night:** M5 (+ M6 if on track)
- **Sun morning:** M7, rehearse the demo

The submission deadline is in §9. Don't assume one.
