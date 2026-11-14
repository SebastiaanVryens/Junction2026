# FactsLens: Hostinger "Decode the Noise"

**Live URL:** https://srv2159312.hstgr.cloud/ · our error rate: /accuracy · every figure: /metrics · the swarms: /network

**What it is:** a live newspaper and monitoring service about coordinated propaganda. It watches the whole public Bluesky stream (about 4,000 posts and reposts a minute), 40 Telegram channels the Pravda network republishes, the 78 Mastodon servers on IFTAS's Portal Kombat list, and the Pravda network's own news sites. It finds **swarms** (groups of accounts acting together), explains each with evidence, compares how state media and independent newspapers tell the same events, lets anyone check a link or ask a question, and publishes its own precision and false-positive rate. Directions combined: **spot the swarm** (core), **name the technique**, **trust the source**.

**Runs on Hostinger:** the whole application, seven services in Docker Compose, runs on the Hostinger VPS (KVM 2, 2 vCPU, 8 GB, no GPU) behind Traefik with Let's Encrypt. Kafka and PostgreSQL are Aiven services; the language models are NVIDIA's hosted Nemotron. Load test: 50 simultaneous visitors, 300 page views in 9 s, 0 errors.

## Detection logic

**Stage 1, on every post** (60-minute window, rebuilt from Kafka on every restart): near-identical text (MinHash), the same link or website, the same uncommon hashtag set, the same fediverse server, repost rings, and **the same claim in several languages** (Nemotron Embed, 3+ accounts in 2+ languages). A group becomes a candidate above a gate.

**Stage 2, explainable scoring.** Every signal is stored with its value, threshold and a sentence, in four categories: coordination (copy-paste, synchrony, repost ring, repetition, cross-group reuse, shared hashtags, translated claim); account (new accounts, creation burst, low reach, empty profiles, template bios, accounts the platform removed); provenance (IFTAS-listed accounts and servers, VIGINUM/CheckFirst propaganda domains, Pravda Telegram sources, Telegram-first content); disclosure (declared bots). Tiers: suspicious, watch, likely organic, declared bots.

**Then Nemotron investigates:** pictures and video, linked pages and domain age, an independent news check per claim, techniques with exact quotes (loaded language, fear appeals, whataboutism, fabricated authority), and a staged / organic / insufficient-evidence verdict.

## False-positive handling

- **Coordination alone is never suspicious:** account or provenance evidence must add to it (a share button "coordinates" real people too).
- **Declared bots** get their own tier; they were our first live false positives.
- **Nemotron writes the innocent explanation first**, may abstain, and may not clear documented propaganda infrastructure.
- **People check our work:** reviewers confirm (by type), mark false alarms, or mark single accounts as real. A **random sample** mode measures the false-positive rate without our choice of clusters; the queue serves groups on our topics first.
- **Corrections are published:** every false positive we found and what we changed (eight so far) is on /accuracy.
- **No accusations:** groups are leads for people; names only for cases already on public lists.

## Measured results (units stated)

- **Precision of our flags** (unit: cluster, human review, reviewers' choice): **93% (14 of 15)**; on our topics (war, Russia/Ukraine, NATO, EU, Baltics): **100% (6 of 6)**.
- **False-positive rate, random sample** (unit: cluster): **0 of 3** not-coordinated clusters flagged suspicious. Too few to state as a rate; the sample grows on /accuracy as the team reviews.
- **With Nemotron's judge:** precision 91% (10 of 11).
- **Documented accounts** (unit: IFTAS-listed account posting in a 3-hour replay): **12 of 17 found**, a median of 10 to 21 minutes after their first post.
- **Self-test** (unit: campaign; 12 fictional campaigns by Nemotron hidden in 1.1 million real posts and reposts): **4/4 copy-paste and 4/4 reworded found; 0/4 written to evade**, our stated blind spot (we tried meaning-based grouping and measured why it does not catch them).
- **Live since Saturday 05:10 UTC:** 3,860 accounts in flagged groups, 25 of them on IFTAS's list, **203 on the same network servers but on no public list**, 54 already removed by the platform; 167 groups judged staged; 21 campaigns.

## Real-world impact

FactsLens shows moderators which accounts to act on first and why, shows readers who is behind what reaches them, and shows how the Pravda network launders a claim from Telegram into Finnish, Estonian and Latvian. Every figure on the site states what it counts and how many.
