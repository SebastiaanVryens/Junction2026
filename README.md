# The Puppet Post

*See who's really behind the post.*

**Live:** https://thepuppetpost.tech/ · **Accuracy & Corrections:** [/accuracy](https://thepuppetpost.tech/accuracy) · **Full metrics:** [/metrics](https://thepuppetpost.tech/metrics)

The Puppet Post is a live newspaper about influence operations. It reads public posts on Bluesky, Telegram and Mastodon and the Pravda network's own sites, finds **swarms** (groups of accounts acting together, not single posts), compares how state media and independent newspapers tell the same events, and lets anyone check a link or ask a question. **NVIDIA Nemotron** does the reading, comparing and explaining; people make the judgement.

> Other tools tell you a campaign happened. The Puppet Post shows who is behind a post, how the network operates, and what independent newspapers say, in plain language for everyone.

## What you see

- **Front page:** the latest contested stories, with recap, pictures or video, who says what and where they contradict.
- **Contested Stories:** Russian state media (TASS, RT, Lenta.ru, RBC), the Pravda network's own sites (Finland, Sweden, Estonia, Latvia, Germany, English) and its Telegram channels, against independent outlets found through Google News in 36 national editions. Every 15 minutes; every point footnoted to its source. It shows how a claim is laundered from Telegram into other languages, and which groups of accounts spread which version.
- **Look for the Truth** ([/lens](https://thepuppetpost.tech/lens)): paste any post or article. Who is behind it, whether a network pushes it, what independent newspapers say about each claim, and the persuasion techniques with the exact words. About a minute.
- **Ask the Truth** ([/ask](https://thepuppetpost.tech/ask)): a question in plain words, e.g. *"Is the war between Ukraine and Russia ending?"*. Nemotron searches independent news and our live data with read-only tools and answers with every source linked.
- **The Network** ([/network](https://thepuppetpost.tech/network)): the swarms of the last 72 hours, with the signals that fired in plain words and Nemotron's verdict, the campaigns, where they come from, the websites they push, and the reviewer's desk.
- **Accuracy & Corrections** ([/accuracy](https://thepuppetpost.tech/accuracy)): our precision and false-positive rate with their sample sizes, our self-test, live figures from the last 72 hours, every correction we made, and where we are blind.

## How it works

1. **Ingests live:** every Bluesky post and repost (Jetstream), the Telegram channels the Pravda network republishes most, the Mastodon servers on IFTAS's Portal Kombat list, and the Pravda sites' public feeds, all through one Aiven Kafka stream: 3,600 to 6,000 posts and reposts a minute, depending on the hour (measured on our status page, [/pipeline](https://thepuppetpost.tech/pipeline)).
2. **Detects swarms** with explainable signals: near-identical text (MinHash), shared links and hashtag sets, repost rings, synchronised timing, the same claim in several languages (Nemotron Embed), new or empty accounts, accounts already removed by Bluesky, known propaganda domains, and content that appeared on Telegram first. Each signal is shown with its value and threshold; coordination alone is never enough.
3. **Investigates with Nemotron 3.5 Super VL**, an agent with tools: it looks at images and video, reads linked pages and domain registrations, checks each claim against independent news in the relevant national editions, names manipulation techniques with exact quotes, and weighs the innocent explanation before judging *staged*, *organic* or *insufficient evidence*. Statements without evidence it was shown are removed automatically.
4. **Links clusters into campaigns** and **compares contested stories** (above).
5. **Measures itself:** synthetic campaigns written by Nemotron (fictional, never posted) hidden in real recorded traffic, accounts from IFTAS's documented list as ground truth, human reviews for precision, and a random sample for the false-positive rate.

It never accuses anyone automatically. Groups of accounts are leads for human review; names, posts and pictures of individual accounts are shown only for cases that are already public. Anyone can review a group: visitors' reviews are counted apart from our figures and never reach Nemotron's prompt. The newsroom pages (clusters, campaigns, the 3D graph) are reachable by link.

**Privacy:** all data stays in the EU (Hostinger in Düsseldorf, Aiven in Amsterdam and Frankfurt); account ids are stored as keyed hashes; raw posts, names and reports are deleted after 72 hours.

## Results

**Self-test (14 Nov 2026):**
- **Documented accounts:** in each of two 3-hour test windows, 17 accounts from IFTAS's Portal Kombat list posted, and our detector flagged **12** of them, a median **10 and 21 minutes** after each account's first post.
- **Synthetic campaigns:** copy-paste campaigns were caught 4/4 as suspicious within 1–2 minutes, and paraphrased ones 4/4 for watch within 13 minutes. Evasive campaigns (own words, several languages, aged accounts) are not caught yet: a measured blind spot ([`docs/EVAL.md`](docs/EVAL.md)).

**Human review** (live page, 15 Nov 2026, 13:30 Helsinki time; unit: cluster):
- **95%** of the groups we flag as suspicious were confirmed by a reviewer (18 of 19); on our topics 6 of 6. Reviewers chose these groups, so this is not a random sample.
- **False-positive rate on a random sample: 0 of 8** not-coordinated groups were flagged suspicious (15 random reviews; the margin is wide).

**Live, the last 72 hours** (same time):
- **41** accounts from IFTAS's documented Pravda list caught posting and flagged, plus 302 accounts on the same servers that are not on the list (leads, not proof). Unit: account.
- Bluesky has since removed **5.2%** of the accounts in groups we flag as suspicious (165 of 3,201), against **0.29%** in groups we call organic (70 of 23,972): **18× as often**. Groups flagged *because* accounts were removed are left out. Unit: account.
- Nemotron judged **373 of 985** investigated groups staged; it called the others organic or said the evidence was not enough. Unit: cluster.
- **92%** of the evidence Nemotron cites was really in front of it (834 reports with prompt v6); the rest is removed before publication. Unit: cited reference.

The submitted version is tagged `junction-2026-submission`; the figures above are from after the event.

## Built with

- **Hostinger VPS:** the whole stack runs here: seven services in Docker Compose behind Traefik, 2 vCPU, 8 GB, no GPU.
- **NVIDIA Nemotron 3.5 Super VL** (NVIDIA Build): investigator agent with tools, real-or-staged judge, campaign analyst, story analyst, Look for the Truth, Ask the Truth, picture checker, red-team campaign writer and translator for reviewers. **Nemotron 3.5 Embed:** the same claim across languages, and one story per event. How we improved its output: [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md).
- **Aiven for Apache Kafka:** carries every post from three platforms; after a deploy the detector rewinds an hour, so it never goes blind. **Aiven for PostgreSQL:** the shared state of the seven services (groups, reports, reviews, evaluation results) and the 72-hour deletion.
- **Ground truth:** VIGINUM and CheckFirst propaganda-domain lists, CheckFirst's Pravda Telegram sources, IFTAS's Portal Kombat account list.

Junction 2026 entry for Hostinger *Decode the Noise*, NVIDIA *Best Use of Nemotron* and Aiven *Best Use of Aiven*.

## Run it

```
uv run infra/check_access.py     # needs your own .env; shows which settings work
uv run pytest
docker compose up -d --build     # or infra/deploy.sh for the VPS
```

The code name in the repository is `factslens`, the project's first name.

## Docs

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) architecture · [`docs/EVAL.md`](docs/EVAL.md) evaluation · [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) Nemotron changes and their effect · [`docs/DECISIONS.md`](docs/DECISIONS.md) decision log · [`docs/DEMO.md`](docs/DEMO.md) demo script · [`docs/submissions/`](docs/submissions/) submission texts · [`docs/PLAN.md`](docs/PLAN.md) plan · [`CLAUDE.md`](CLAUDE.md) full brief
