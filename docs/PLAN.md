# Build plan (v3, Sat 14 Nov ~16:00)

Project: **FactsLens**, *"see who's really behind the post."* Brief: [`CLAUDE.md`](../CLAUDE.md). Decision log: [`DECISIONS.md`](DECISIONS.md). Evaluation: [`EVAL.md`](EVAL.md). Nemotron changes: [`IMPROVEMENTS.md`](IMPROVEMENTS.md).

**Changed since v2 (Fri night):**
- M1–M4 are built and live. **M5 (spread forecast + Nemotron job 4) is dropped**; campaign reports give its moderator value ("which accounts to act on first"). **M6 is partly built** (recycled pictures within our own data).
- Added on the way, at the team's request: Telegram and Mastodon sources, an independent news check, campaign reports, contested stories, recycled pictures, translation and review tools.
- Left: M7 (docs, submission texts, demo), plus human reviews for the precision numbers.

---

## 0. Where we are

| Part | Status |
|---|---|
| Live site | ✅ https://srv2159312.hstgr.cloud/ on the Hostinger VPS (Docker Compose behind the template's Traefik, Let's Encrypt). Own domain not claimed yet. |
| Sources | ✅ the whole public Bluesky stream (~4,000 posts and reposts a minute), 40 Telegram channels the Pravda network republishes, 78 IFTAS-listed Mastodon servers (~59 reachable) |
| Aiven | ✅ Kafka (topic `posts`) and PostgreSQL 18.7. Billing checked by the team: an estimated 0.11 of the $50 credit per month |
| Nemotron 3.5 Super VL | ✅ NVIDIA Build, one key, own cap 20 calls/min and 300/h per service |
| Tests | 177 passing (ruff, mypy strict) |

---

## 1. What Nemotron does (the spine of the product and the demo)

| Job | What it does | Status | How it is measured |
|---|---|---|---|
| **1. Investigator agent** | Per suspicious cluster: reads posts, anonymised accounts and FactsLens's signals; looks at full-size images and up to 2 min of video; calls tools (`fetch_page`, `domain_info`, `search_news`); names techniques with exact quotes; checks each factual claim against independent news in the relevant languages. Every claim must cite evidence; ungrounded items are removed. | ✅ live (prompt v6) | Fixed 12-cluster set, versions compared (EVAL.md): grounding 91–98%, no organic control called staged after v3; run-to-run noise measured |
| **2. Real-or-staged judge** | Same call: strongest innocent explanation first, then *staged / organic / insufficient evidence*. A rule stops it from clearing clusters with documented-network evidence. | ✅ live | Precision and false-positive rate with and without the judge, from human reviews (metrics page): **needs reviews** |
| **3. Red team** | Writes synthetic campaigns (fictional country, `.example` links, three difficulty levels, evasion tactics), replayed into recorded traffic, never posted. | ✅ | Recall per difficulty: simple 4/4, paraphrased 4/4 (watch), evasive 0/4 (EVAL.md) |
| ~~4. Audience modelling for a forecast~~ | Dropped with M5. | ❌ | – |
| **Campaign analyst** | Reads all clusters that share accounts at once (long context): one campaign or not, what it pushes, how it is run, **which accounts to review first**. | ✅ live, every 15 min | First report: 40 clusters → one begging-scam campaign of 25 accounts |
| **Story analyst** | Compares how state media (TASS, RT, Lenta.ru, RBC mirrors), Pravda-republished Telegram channels, independent outlets and our clusters tell the same event, including photos: contradictions, claims only state media make, who amplifies which version. Does not decide who is right. | ✅ live, every 15 min | Reviewers can check stories; no automatic score yet |
| **Picture checker** | When the same picture is posted again by another account with a different caption, says which caption the picture fits, with visible clues. | ✅ live | Distance threshold measured on 399 of our images |
| **Translator** | For reviewers: translates a cluster's posts, keeping loaded words. | ✅ live | – |

**Feedback loop:** reviewer decisions (with type: propaganda / bots / spam / other) and notes go into the investigator's prompt as calibration examples.

---

## 2. Decisions (approved Fri night; what changed)

| # | Decision | Outcome |
|---|---|---|
| D1 | Deploy over SSH with `infra/deploy.sh` | ✅ as planned |
| D2 | HTTPS with Caddy | Changed: the VPS template already runs **Traefik**; the app registers through labels |
| D3 | Kafka: trimmed stream, 24 h; Postgres: only candidate clusters | ✅; raw ids, names, posts, stories and pictures deleted after 72 h |
| D4 | Two-stage detection | ✅; more signals than planned (hashtag sets, servers, Telegram-first, IFTAS lists) |
| D5 | One package, one image, a service per entry point | ✅ (ingest, telegram, mastodon, detector, enrich, investigator, api) |
| D6 | FastAPI + Jinja2 + HTMX, Cytoscape map | Partly: no network map; a "clusters sharing accounts" table and campaign pages instead |
| D7 | `kafka-python` | ✅ |
| D8 | Investigator queue in Postgres | ✅ (highest score first; re-investigated when a cluster grows) |
| D9 | One replay tool for evaluation and demo backup | ✅ (`python -m factslens.eval.run`) |

---

## 3. Repo structure (as built)

```
src/factslens/
  common/        settings, event model, Kafka/Postgres helpers, HMAC account ids, 72 h retention
  ingest/        Jetstream (server rotation), Telegram channel pages, Mastodon timelines
  detector/      stage-1 groups (text, link, domain, hashtag set, server, repost ring), scoring
  enrich/        Bluesky and Mastodon profiles, public lists (VIGINUM, CheckFirst, IFTAS)
  investigator/  Nemotron client, evidence + tools, report schema + grounding, campaigns, stories,
                 recycled pictures, translation
  api/           FastAPI pages: status, clusters, campaigns, stories, metrics, review
  eval/          red-team generator, replay runner, report-quality scorer
migrations/      001–010, applied on start
eval/            red-team campaigns, results, report-quality set, ground-truth fetcher (data not committed)
infra/           check_access.py, deploy.sh
```

---

## 4. Milestones

| | Milestone | Status |
|---|---|---|
| M1 | Ingest → Kafka → detector, public URL | ✅ |
| M2 | Swarm signals + Postgres + UI | ✅ |
| M3 | Nemotron jobs 1 + 2 + review UI | ✅ |
| M4 | Evaluation + metrics page (+ job 3, feedback loop) | ✅ red-team runs 1–4, documented accounts, quality set, live findings. **Open: precision on real traffic needs human reviews.** |
| M5 | Forecast + job 4 | ❌ dropped (DECISIONS.md, 14 Nov) |
| M6 | Recycled footage | ◐ within our own data (perceptual hashes + Nemotron's judgement). Not built: "first seen" on the wider web; it needs Google Cloud Vision (billing account; team decision pending). |
| M7 | Final deploy, docs, submissions, demo | ⏳ see below |

**M7, remaining work:**

| Task | Time | When |
|---|---|---|
| `docs/ARCHITECTURE.md` | 45 min | Sat evening |
| `docs/submissions/hostinger.md`, `nvidia.md`, `aiven.md` | 1.5 h | Sat evening |
| Demo script (§5) and a results slide | 45 min | Sat night |
| Update numbers (live findings, precision from reviews) in README, EVAL, submissions | 30 min | Sun 07:00 |
| Rotate the reviewer key, deploy freeze, rehearse | 30 min | Sun 08:00 |

---

## 5. Demo (2–3 min)

1. **Status page:** three platforms, ~4,000 posts a minute, everything running on Hostinger with Kafka and PostgreSQL on Aiven.
2. **A contested story:** TASS / Telegram vs independent outlets side by side, contradictions, claims only state media make, the photos, and which of our clusters push which version.
3. **A cluster:** the signals that fired with values and thresholds; Nemotron's report (pictures, news check per claim, techniques with quotes, innocent explanation, verdict); translate button; recycled-picture note if any.
4. **A campaign:** 40 alerts → one campaign, with the accounts to review first.
5. **Metrics:** live findings (e.g. accounts on network servers that are on no public list), red-team recall per difficulty, the quality measurement with its honest noise result, precision and false-positive rate from reviews.

---

## 6. Budgets (measured)

- **VPS:** about 1 GB of 7.9 GB used, CPU mostly idle, disk 6%.
- **Nemotron:** ~165 calls/hour for investigations, plus campaigns, stories and pictures every 5–15 min. One key; we hit HTTP 429 when three processes shared it, so no local evaluation runs during the demo.
- **Aiven:** ~0.11 of $50 per month (team check, Sat).
- **Database:** ~20 MB.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| No human reviews → precision empty on the metrics page | Team reviews 40–60 clusters with the review bar (types, skip, keys 1–4/F/U/S) |
| NVIDIA rate limit (one key) | Own caps; periodic rounds survive errors; no local runs during the demo; ask whether teammates' keys may be pooled |
| Nemotron verdicts vary between runs | Documented (EVAL.md); grounding and rules (provenance guard, staged needs grounded claims) do not depend on chance |
| Quiet stream during judging | Replay tool; contested stories and campaigns persist for 72 h |
| "Hosted Nemotron and Aiven are not on Hostinger" | Ask the Hostinger mentors; the app itself runs only on the VPS |

---

## 8. Open items

| Item | Who | Needed by |
|---|---|---|
| 40–60 reviews | team | Sun 07:00 |
| Google Cloud Vision for web "first seen" (billing account) yes/no | team | if yes, Sat evening |
| Hostinger mentors: hosted Nemotron + Aiven counts as "runs on Hostinger"? | team | before submission |
| NVIDIA: may we pool teammates' keys? | team | only if rate limits bite |
| Free domain | team | optional |
| Rotate the reviewer key | Claude | Sun 08:00 |
