# FactsLens

*See who's really behind the post.*

**Live:** https://srv2159312.hstgr.cloud/ · **Metrics:** [/metrics](https://srv2159312.hstgr.cloud/metrics)

FactsLens is a live radar for influence operations. It watches public posts on Bluesky, Telegram and Mastodon, finds **swarms** (groups of accounts acting together, not single posts), and has **NVIDIA Nemotron** investigate each one for a human moderator.

> Other tools tell you a campaign happened. FactsLens shows who is behind it, how they operate, and which accounts to look at first.

## What it does

1. **Ingests live:** every Bluesky post and repost (Jetstream), the Telegram channels the Pravda network republishes most, and the Mastodon servers on IFTAS's Portal Kombat list.
2. **Detects swarms** with explainable signals: near-identical text, shared links and hashtag sets, repost rings, synchronised timing, new or empty accounts, accounts already removed by Bluesky, known propaganda domains, and content that appeared on Telegram first. Every cluster page shows which signals fired, with values and thresholds.
3. **Investigates with Nemotron 3.5 Super VL**, as an agent with tools:
   - looks at the images and video, and reads the linked pages and domain registrations;
   - checks whether independent news outlets report each claim, in the relevant languages, and flags outlets on propaganda lists;
   - names manipulation techniques (loaded language, fear appeals, whataboutism, fabricated authority), with the exact words quoted;
   - weighs the strongest innocent explanation first, then judges *staged*, *organic* or *insufficient evidence*.

   Every claim in a report must cite evidence it was shown; anything else is removed automatically.
4. **Measures itself:** Nemotron writes synthetic test campaigns (fictional, never posted) that are mixed into real recorded traffic, and real documented accounts are used as ground truth. Results are on the public metrics page.

It never accuses anyone automatically. Every cluster is a lead for a human reviewer, and account names are public only for cases that are already public.

## Results so far (14 Nov 2026)

- **Documented accounts:** 16 accounts from IFTAS's Portal Kombat list posted in a 3-hour test window; FactsLens flagged **11** (8 suspicious, 3 watch), a median **10 minutes** after each account's first post.
- **Synthetic campaigns:** copy-paste campaigns were caught 4/4, within 1–2 minutes. Paraphrased and evasive campaigns are not caught yet: an honest, measured blind spot ([`docs/EVAL.md`](docs/EVAL.md)).
- **Live, on no public list:** 14 accounts on an IFTAS-listed Mastodon server, 13 of them under 60 days old, pushing war content in four languages. Rated suspicious; Nemotron judged it staged.
- **Precision on real traffic** needs human labels; the metrics page shows it as reviewers confirm or reject clusters.

## Built with

- **Hostinger VPS:** the whole stack runs here (Docker Compose, 2 vCPU, 8 GB, no GPU).
- **NVIDIA Nemotron 3.5 Super VL** (NVIDIA Build): investigator, real-or-staged judge, red-team campaign writer. How we improved its output: [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md).
- **Aiven for Apache Kafka** (the event stream between services) and **Aiven for PostgreSQL** (clusters, reports, reviews, evaluation results).
- **Ground truth:** VIGINUM and CheckFirst propaganda-domain lists, CheckFirst's Pravda Telegram sources, IFTAS's Portal Kombat account list.

Junction 2026 entry for Hostinger *Decode the Noise*, NVIDIA *Best Use of Nemotron* and Aiven *Best Use of Aiven*.

## Run it

```
uv run infra/check_access.py     # needs your own .env
uv run pytest
docker compose up -d --build     # or infra/deploy.sh for the VPS
```

## Docs

[`docs/EVAL.md`](docs/EVAL.md) evaluation · [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) Nemotron changes and their effect · [`docs/DECISIONS.md`](docs/DECISIONS.md) decision log · [`docs/PLAN.md`](docs/PLAN.md) plan · [`CLAUDE.md`](CLAUDE.md) full brief
