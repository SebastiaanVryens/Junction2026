# The Puppet Post

*See who's really behind the post.*

**Live:** https://thepuppetpost.tech/ · **Accuracy & Corrections:** [/accuracy](https://thepuppetpost.tech/accuracy) · **Full metrics:** [/metrics](https://thepuppetpost.tech/metrics)

The Puppet Post is a live newspaper about influence operations. It reads public posts on Bluesky, Telegram and Mastodon and the Pravda network's own sites, finds **swarms** (groups of accounts acting together, not single posts), compares how state media and independent newspapers tell the same events, and lets anyone check a link or ask a question. **NVIDIA Nemotron** does the reading, comparing and explaining; people make the judgement.

> Other tools tell you a campaign happened. The Puppet Post shows who is behind a post, how the network operates, and what independent newspapers say, in plain language for everyone.

## What you see

- **Front page:** the latest contested stories, with recap, pictures or video, who says what and where they contradict.
- **Contested Stories:** Russian state media (TASS, RT, Lenta.ru, RBC), the Pravda network's own sites (Finland, Sweden, Estonia, Latvia, Germany, English) and its Telegram channels, against independent outlets in several countries. Every 15 minutes; every point footnoted to its source. It shows how a claim is laundered from Telegram into other languages, and which groups of accounts spread which version.
- **The Network** ([/network](https://thepuppetpost.tech/network)): the swarms of the last 72 hours, with the signals that fired in plain words and Nemotron's verdict, the campaigns, where they come from, the websites they push, and the reviewer's desk.
- **Look for the Truth** ([/lens](https://thepuppetpost.tech/lens)): paste any post or article. Who is behind it, whether a network pushes it, what independent newspapers say about each claim, and the persuasion techniques with the exact words. About a minute.
- **Ask the Truth** ([/ask](https://thepuppetpost.tech/ask)): a question in plain words, e.g. *"Is the war between Ukraine and Russia ending?"*. Nemotron searches independent news and our live data with read-only tools and answers with every source linked.
- **Accuracy & Corrections** ([/accuracy](https://thepuppetpost.tech/accuracy)): our precision and false-positive rate with their sample sizes, documented accounts found, our self-test, every correction we made, and where we are blind.

## How it works

1. **Ingests live:** every Bluesky post and repost (Jetstream), the Telegram channels the Pravda network republishes most, the Mastodon servers on IFTAS's Portal Kombat list, and the Pravda sites' public feeds.
2. **Detects swarms** with explainable signals: near-identical text, shared links and hashtag sets, repost rings, synchronised timing, new or empty accounts, accounts already removed by Bluesky, known propaganda domains, and content that appeared on Telegram first. Each signal is shown with its value and threshold.
3. **Investigates with Nemotron 3.5 Super VL**, an agent with tools: it looks at images and video, reads linked pages and domain registrations, checks each claim against independent news in the relevant national editions, names manipulation techniques with exact quotes, and weighs the innocent explanation before judging *staged*, *organic* or *insufficient evidence*. Claims without evidence it was shown are removed automatically.
4. **Links clusters into campaigns** and **compares contested stories** (above).
5. **Measures itself:** synthetic campaigns written by Nemotron (fictional, never posted) hidden in real recorded traffic, accounts from IFTAS's documented list as ground truth, and human reviews for precision and false positives.

It never accuses anyone automatically. Groups of accounts are leads for human review; account names are shown only for cases that are already public. The newsroom pages (clusters, campaigns, the 3D graph, the review queue) are reachable by link for moderators and judges.

## Results so far (14 Nov 2026)

- **Documented accounts:** in each of two 3-hour test windows, 17 accounts from IFTAS's Portal Kombat list posted, and our detector flagged **12** of them, a median **10 and 21 minutes** after each account's first post.
- **Synthetic campaigns:** copy-paste campaigns were caught 4/4 as suspicious within 1–2 minutes, and paraphrased ones 4/4 for watch within 13 minutes. Evasive campaigns (own words, several languages, aged accounts) are not caught yet: a measured blind spot ([`docs/EVAL.md`](docs/EVAL.md)).
- **Live, on no public list:** 14 accounts on an IFTAS-listed Mastodon server, 13 of them under 60 days old, pushing war content in four languages. Rated suspicious; Nemotron judged it staged.
- **Precision on real traffic** needs human labels; the metrics page shows it as reviewers confirm or reject clusters.

## Built with

- **Hostinger VPS:** the whole stack runs here (Docker Compose, 2 vCPU, 8 GB, no GPU).
- **NVIDIA Nemotron 3.5 Super VL** (NVIDIA Build): investigator, real-or-staged judge, campaign analyst, red-team campaign writer, translator for reviewers, the Lens's plain-language report, and the analyst behind Ask the network. How we improved its output: [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md).
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
