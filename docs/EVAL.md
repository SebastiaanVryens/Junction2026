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
