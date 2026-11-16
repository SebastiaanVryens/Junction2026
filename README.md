# The Puppet Post

*See who's really behind the post.*

**Live:** https://thepuppetpost.tech/ (the paper edition) · **Junction 2026 version:** tag [`junction-2026-submission`](https://github.com/SebastiaanVryens/Junction2026/tree/junction-2026-submission)

The Puppet Post is a live newspaper about influence operations. Every 15 minutes NVIDIA Nemotron compares how Russian state media, the Pravda network's own sites and the Telegram channels it republishes tell the news with how independent newspapers tell it: the text, the pictures and the video. It shows who says what, where they contradict each other and which claims only state media make. It does not decide who is right: every point cites its sources, so readers can judge for themselves.

It began at Junction 2026 in Espoo as a full detector of **swarms** (groups of accounts acting together) on Bluesky, Telegram and Mastodon. That full edition is still in this repository; the live site now runs the lighter **paper edition**.

> Other tools tell you a campaign happened. The Puppet Post shows who is behind a post, how the network operates, and what independent newspapers say, in plain language for everyone.

## Two editions

| | Paper edition (live now) | Full edition (Junction 2026) |
|---|---|---|
| Pages | Front Page, Contested Stories, How We Work | also Look for the Truth, Ask the Truth, The Network, Accuracy & Corrections |
| Reads | state media, the Pravda sites, Telegram channels, Google News | also the whole public Bluesky stream (3,600 to 6,000 posts a minute) and 78 Mastodon servers |
| Runs as | one service that writes static pages every 15 minutes, served by nginx | seven services with Aiven Kafka and PostgreSQL |
| Server memory | about 0.7 GB | about 2.5 GB |
| Start it | `EDITION=paper infra/deploy.sh` | `infra/deploy.sh` |

## What you see (paper edition)

- **Front Page:** the latest compared stories in a fluid newspaper grid: the lead story with its picture or video, then the others in reading order.
- **Contested Stories:** every story compared in the last 24 hours, labelled *sources contradict each other*, *sources agree*, *only state media report it* or *too little coverage to compare*. Each story page lists who says what, where they contradict, what all sides agree on, and every source as a numbered footnote.
- **How We Work:** what we read, how a story is compared, and the rules we keep.

## How it works

**Paper edition** (`src/factslens/paper/`), every 15 minutes:
1. **Reads the headlines** of Russian state media (TASS, RT, Lenta.ru, RBC), the Pravda network's sites (Finland, Sweden, Estonia, Latvia, Germany, English) and the 16 Telegram channels the network republishes most.
2. **Nemotron 3.5 Super VL picks** up to four stories that outlets in other countries also report, skipping what was compared in the last 18 hours; **Nemotron Embed** drops repeats of the same event.
3. **Searches independent coverage** in the relevant national editions of Google News (36 in all), in their own languages; outlets on VIGINUM's and CheckFirst's propaganda lists are marked and never count as independent.
4. **Compares** text, pictures and video. Every point must cite the evidence it rests on; a point that cites nothing Nemotron was shown is removed before printing.
5. **Writes the site as plain HTML** with the full edition's own templates, so a static web server (or GitHub Pages) can serve it. Stories are kept 7 days.

**Full edition** (Junction 2026):
1. **Ingests live** every Bluesky post and repost, the Pravda network's Telegram channels and Mastodon servers, and the Pravda sites, through one Aiven Kafka stream.
2. **Detects swarms** with explainable signals: near-identical text (MinHash), shared links and hashtag sets, repost rings, synchronised timing, the same claim in several languages (Nemotron Embed), new or empty accounts, accounts removed by Bluesky, known propaganda domains, and content that appeared on Telegram first. Each signal is shown with its value and threshold; coordination alone is never enough.
3. **Investigates with Nemotron 3.5 Super VL**, an agent with tools: images and video, linked pages and domain registrations, independent news per country, manipulation techniques with exact quotes, and the innocent explanation first before judging *staged*, *organic* or *insufficient evidence*.
4. **Links groups into campaigns**, and lets anyone check a link (Look for the Truth) or ask a question (Ask the Truth).
5. **Measures itself:** synthetic campaigns written by Nemotron, accounts from IFTAS's documented list, human reviews for precision, and a random sample for the false-positive rate.

It never accuses anyone automatically: groups of accounts were leads for human review, and names were shown only for cases that were already public.

**Privacy:** the paper edition stores nothing about private people. The full edition kept all data in the EU (Hostinger in Düsseldorf, Aiven in Amsterdam and Frankfurt), stored account ids as keyed hashes, and deleted raw posts, names and reports after 72 hours.

## Results of the full edition

**Self-test (14 Nov 2026):**
- **Documented accounts:** in each of two 3-hour test windows, 17 accounts from IFTAS's Portal Kombat list posted, and our detector flagged **12** of them, a median **10 and 21 minutes** after each account's first post.
- **Synthetic campaigns:** copy-paste campaigns were caught 4/4 as suspicious within 1–2 minutes, and paraphrased ones 4/4 for watch within 13 minutes. Evasive campaigns (own words, several languages, aged accounts) were not caught: a measured blind spot ([`docs/EVAL.md`](docs/EVAL.md)).

**Human review** (15 Nov 2026, 13:30 Helsinki time; unit: cluster):
- **95%** of the groups we flagged as suspicious were confirmed by a reviewer (18 of 19); on our topics 6 of 6. Reviewers chose these groups, so this is not a random sample.
- **False-positive rate on a random sample: 0 of 8** not-coordinated groups were flagged suspicious (15 random reviews; the margin is wide).

**Live, the last 72 hours** (same time):
- **41** accounts from IFTAS's documented Pravda list caught posting and flagged, plus 302 accounts on the same servers that are not on the list (leads, not proof). Unit: account.
- Bluesky had removed **5.2%** of the accounts in groups we flagged as suspicious (165 of 3,201), against **0.29%** in groups we called organic (70 of 23,972): **18× as often**. Groups flagged *because* accounts were removed are left out. Unit: account.
- Nemotron judged **373 of 985** investigated groups staged; it called the others organic or said the evidence was not enough. Unit: cluster.
- **92%** of the evidence Nemotron cited was really in front of it (834 reports with prompt v6); the rest was removed before publication. Unit: cited reference.

The submitted version is tagged `junction-2026-submission`; the figures above are from after the event.

## Built with

- **Hostinger VPS:** runs either edition with Docker Compose behind Traefik (2 vCPU, 8 GB, no GPU); the paper edition adds nginx.
- **NVIDIA Nemotron 3.5 Super VL** (NVIDIA Build): the story analyst in both editions; in the full edition also the investigator agent, real-or-staged judge, campaign analyst, Look for the Truth, Ask the Truth, picture checker, red-team campaign writer and translator for reviewers. **Nemotron 3.5 Embed:** one story per event, and the same claim across languages. How we improved its output: [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md).
- **Aiven for Apache Kafka and PostgreSQL** (full edition): Kafka carried every post from three platforms and let the detector rewind an hour after each deploy; PostgreSQL held the shared state of the seven services and the 72-hour deletion.
- **Ground truth:** VIGINUM and CheckFirst propaganda-domain lists, CheckFirst's Pravda Telegram sources, IFTAS's Portal Kombat account list.

Built at Junction 2026 for Hostinger *Decode the Noise*, NVIDIA *Best Use of Nemotron* and Aiven *Best Use of Aiven*.

## Run it

```
uv run pytest
uv run eval/ground_truth/fetch.py                      # the public propaganda lists (not committed)
uv run python -m factslens.paper --once                # one paper round and build: data/paper/site
uv run python -m factslens.paper --build-only          # rewrite the pages, no Nemotron calls
EDITION=paper infra/deploy.sh                          # the paper edition on the VPS
infra/deploy.sh                                        # the full edition (needs Aiven Kafka and PostgreSQL)
uv run infra/check_access.py                           # which of your .env settings work
```

Settings come from your own `.env`. The paper edition needs only `NVIDIA_API_KEYS`, plus the public lists from `fetch.py` for its Telegram channels and propaganda flags (without them it still runs, with neither); the full edition also needs Aiven, and `fetch.py --resolve-bluesky` to look up the documented accounts on Bluesky. The code name in the repository is `factslens`, the project's first name.

## Docs

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) architecture of the full edition · [`docs/EVAL.md`](docs/EVAL.md) evaluation · [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) Nemotron changes and their effect · [`docs/DECISIONS.md`](docs/DECISIONS.md) decision log · [`docs/DEMO.md`](docs/DEMO.md) demo script · [`docs/submissions/`](docs/submissions/) submission texts · [`docs/PLAN.md`](docs/PLAN.md) plan · [`CLAUDE.md`](CLAUDE.md) full brief
