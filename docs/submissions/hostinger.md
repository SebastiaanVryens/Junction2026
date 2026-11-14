# FactsLens: Hostinger "Decode the Noise"

*Draft. Fields marked **[FILL]** are filled on Sunday 07:00 from the live metrics page.*

**Live URL:** https://srv2159312.hstgr.cloud/ (metrics: https://srv2159312.hstgr.cloud/metrics)

**What it is:** a monitoring service and dashboard that watches public Bluesky (the whole stream, ~4,000 posts and reposts a minute), 40 Telegram channels the Pravda network republishes, and the 78 Mastodon servers on IFTAS's Portal Kombat list. It finds **swarms**, groups of accounts acting together, explains each one with evidence, and measures its own precision and false-positive rate. Directions combined: **spot the swarm** (core), **name the technique**, **trust the source**.

**Runs on Hostinger:** the whole application (seven services in Docker Compose) runs on the Hostinger VPS (KVM 2) behind its Traefik with Let's Encrypt. Kafka and PostgreSQL are Aiven services, and the language model is NVIDIA's hosted Nemotron.

## Detection logic

**Stage 1, on every post** (60-minute sliding window, in memory):
- near-identical text (MinHash, Jaccard ≥ 0.7);
- the same link or website;
- the same uncommon hashtag set;
- the same fediverse server;
- repost rings.

A group becomes a candidate only above a gate (minimum accounts; for websites, copied text or bridged accounts; platform domains excluded).

**Stage 2, explainable scoring.** Every signal is stored with its value, threshold and a sentence explaining it, in four categories:
- **coordination:** copy-paste, synchrony, repost ring, repetition, cross-cluster reuse, shared hashtag set;
- **account:** new accounts, creation burst, low reach, empty profiles, template bios, accounts the platform removed;
- **provenance:** accounts on IFTAS's list, IFTAS-listed servers, VIGINUM/CheckFirst propaganda domains, Pravda Telegram sources, content that appeared on Telegram first;
- **disclosure:** accounts that say they are bots.

**Tiers:** suspicious, watch, likely organic, declared bots.

**Then Nemotron investigates** the suspicious and watch clusters (NVIDIA submission): images and video, linked pages and domain age, an independent news check per claim, techniques with exact quotes (loaded language, fear appeals, whataboutism, fabricated authority), and a staged / organic / insufficient-evidence verdict. Every claim must cite evidence it was shown.

## False-positive handling

- **Coordination alone is never enough** for *suspicious*: account or provenance evidence must add to it, because a share button's headline "coordinates" real people too.
- **Disclosed bots** (weather alerts, traffic feeds) get their own tier instead of an alarm. The first live false positives were exactly these, and that is how the tier came about.
- **Nemotron writes the strongest innocent explanation before its verdict**, may abstain, and may not clear a cluster tied to documented propaganda infrastructure.
- **Organic controls** (news syndication, users sharing an NHK article, traffic bots) are part of the evaluation set; no prompt version after the first called one of them staged.
- **Human review is built in:**
  - reviewers confirm (by type: propaganda, bots, spam), mark a false alarm or skip, and can mark single accounts as real people;
  - those decisions feed the precision numbers and Nemotron's prompt.
- **Nothing is published as an accusation.** Names are shown publicly only for already-public cases.

## Measured results (units stated)

- **Red-team campaigns** (unit: campaign; 12 written by Nemotron, replayed into 3 hours of real recorded traffic):
  - copy-paste campaigns: **4/4 caught as suspicious** within 1–2 minutes;
  - paraphrased campaigns: **4/4 flagged for watch** within 13 minutes;
  - evasive campaigns (own words, several languages, aged accounts): **0/4**. This is a measured blind spot, stated on the metrics page.
- **Documented accounts** (unit: IFTAS-listed account posting in the window): **12 of 17 flagged** in each of two 3-hour windows, a median of 10 and 21 minutes after each account's first post.
- **Cost on real traffic:** about 47 suspicious and 112 watch clusters an hour. A score threshold of 70 cuts suspicious to about 17 an hour while keeping every copy-paste campaign.
- **Live, since Saturday:** **[FILL]** flagged accounts, of which **[FILL]** are on network servers and on no public list, and **[FILL]** already removed by the platform.
- **Precision on real traffic** (unit: cluster, human-reviewed): **[FILL]% (n = [FILL])**.
- **False-positive rate:** **[FILL]% (n = [FILL])**.
- **With Nemotron's judge:** precision **[FILL]%**, false-positive rate **[FILL]%**.

## Real-world impact

FactsLens shows moderators and fact-checkers which accounts to look at first and why. It links clusters into campaigns, shows where state media and independent outlets tell the same event differently, and flags pictures reused with a new caption. Every number on the metrics page states what it counts and how many there were.
