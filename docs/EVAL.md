# Evaluation

Work in progress. Every number here says what it counts (accounts, groups, posts), on which data, and when it was measured.

## Stage 1: candidate gates (tuned on recorded stream data)

Stage 1 (`src/factslens/detector/groups.py`) groups posts that point at the same thing within a 60-minute window. A group becomes a **candidate** for a closer look when it passes a gate. A gate is not a verdict: it only limits what stage 2 (account profiles, scoring) and Nemotron spend time on.

**Data:** the local recording of Bluesky's public stream (posts + reposts), Thu 12 Nov 2026, 15:00–17:00 UTC, about 375,000 events per hour. Tool: `python -m factslens.detector.replay <files>`.

| Group kind | Gate | Candidates per hour |
|---|---|---|
| TEXT: near-duplicate text (MinHash, Jaccard ≥ 0.7) | ≥ 4 distinct accounts | 149 (15:00), 165 (16:00) |
| LINK: the same link | ≥ 5 accounts and (copied text ≥ 50% of posts, or ≥ 60% within one minute) | 57, 63 |
| REPOST: the same post reposted | ≥ 10 accounts and repost-ring share ≥ 0.8 | 129 (16:00) |

**Why the repost gate looks at rings, not speed.** On 32 minutes of stream, 1,236 posts were reposted by ≥ 10 accounts; almost all are ordinary viral posts. Speed does not separate them: the share of reposts within the busiest minute is ≤ 0.39 for 97% of groups. The *ring share* (accounts that also reposted at least two other posts together with ≥ 30% of the group) does:

| Ring-share threshold | Repost groups passing (32 min) | Distinct original authors |
|---|---|---|
| ≥ 0.5 | 98 | 77 |
| ≥ 0.7 | 33 | 28 |
| ≥ 0.8 | 14 | 13 |

Over a full hour the count is higher (129), because the ring measure grows as more reposts arrive.

**What the gates catch (examples from the recording, not yet verdicts):**
- the same spam text with #art/#photography tags from 42 accounts;
- a "please share or donate" message posted 27 times by 15 accounts;
- identical roofing-company spam from several accounts with the same phone number.

**Known false-positive trap, by design visible here:** many TEXT and LINK candidates are organic. Bluesky's share button pre-fills a news article's headline, so 13 people sharing the same AP story post identical text; weather-alert bots post identical bulletins. Stage 1 cannot tell these apart from staged campaigns; stage 2 (account age, creation bursts, followers, profiles) and Nemotron's real-vs-staged judgement are where that happens, and the metrics page will report how often each gets it wrong.

## Stage 2: first live finding (Sat 14 Nov, 05:00–05:30 UTC)

The first live candidates were families of **disclosed** automated accounts: US National Weather Service forecast mirrors and West Virginia 511 traffic bots. They were created together, have template bios and post in sync, so the account signals (creation burst, template bios) fired and they scored as *suspicious*. That is a false positive for our purpose: their bios say "Unofficial bot sharing alerts from NWS …" and "*Automated feed*".

Fix: a disclosure signal (share of accounts whose bio or handle says they are bots or automated feeds, whole words, several languages) and a separate tier, **declared automation**, with the score capped at 20. Provenance evidence (a listed domain or account) still makes a cluster suspicious, so writing "bot" in a bio cannot hide known propaganda infrastructure. An undisclosed Brazilian deals network (five themed accounts posting the same affiliate link in sync, all created in the same 72 hours) stayed *suspicious*.

**Cost:** about 3,400 events/s on a laptop core including memory tracing (32× real time); peak traced memory about 600 MiB for a full 60-minute window.

## Red-team evaluation, run 1 (Sat 14 Nov, code b536949)

**Setup:** 12 campaigns written by Nemotron (`eval/campaigns/redteam-v1.json`, job 3; fictional country, `.example` links only), 4 per difficulty, injected evenly into 3 hours of recorded Bluesky traffic (Fri 13 Nov 15:00–18:00 UTC, 1,121,223 real events + 124 synthetic posts), run through the production stage-1 and stage-2 code (`python -m factslens.eval.run`). Real account profiles were fetched from Bluesky (7,519 accounts, 130 no longer served). A campaign counts as caught when one cluster holds at least half of its accounts. Full result: `eval/results/run-20261113-1500-3h.json`; also on the public metrics page.

**Campaign recall (unit: campaign):**

| Difficulty | Caught as suspicious | Suspicious or watch | Became a candidate | Minutes to candidate |
|---|---|---|---|---|
| simple (copy-paste, new accounts, 5 min) | 4/4 | 4/4 | 4/4 | 1–2 |
| varied (paraphrased, link variants, 30 min) | 0/4 | 0/4 | 0/4 | – |
| evasive (own words, several languages, aged accounts, 90 min) | 0/4 | 0/4 | 0/4 | – |

**Measured blind spot:** stage 1 only groups near-identical text (Jaccard ≥ 0.7), identical links and repost rings. Paraphrased and evasive campaigns share neither, so they never become candidates. The account evidence that would catch them (new accounts, creation bursts) is only looked at for candidates.

**Real traffic, same 3 hours (unit: cluster of real accounts; not labelled, so not called false positives):** 966 candidates: 117 suspicious (39/h), 286 watch, 52 declared automation, 511 likely organic. The highest-scoring suspicious cluster (score 100) is 6 accounts sharing `geopoliticsprime.substack.com`, including accounts on IFTAS's Portal Kombat list. Others include porn link-spam rings and a home-improvement spam network; at least one looks like organic corporate syndication (legal-news event announcements).

**Threshold:** score ≥ 70 keeps every simple campaign (score 75) and cuts suspicious real clusters to 13.3/h; ≥ 80 loses them all. Precision at any threshold needs human labels (section on human review).

## Documented accounts (IFTAS Portal Kombat list) in our recording

Scan of the full recording (Thu 12 Nov 10:00 – Sat 14 Nov 06:00 UTC, 13.7 M events): **66 listed accounts posted 379 times; 37 of those 66 have since been suspended by Bluesky.** All 377 posts were bridged from Mastodon; 62% carry images or video; languages en, bg, de, ru, zh, es. Only one pair of accounts posted near-identical text, so in this period the network does **not** copy-paste across accounts and stage 1's copy-paste detection cannot catch it. The account fingerprint from CheckFirst's Roska Bridge report (bridged from listed Mastodon servers, new, near-zero followers) is the signal that would.
