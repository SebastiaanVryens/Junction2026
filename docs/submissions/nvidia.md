# The Puppet Post: NVIDIA "Best Use of Nemotron"

*Text for the submission form. Live: https://thepuppetpost.tech/ (code: github.com/SebastiaanVryens/Junction2026)*

## Model used

**NVIDIA Nemotron 3.5 Super VL** (`nvidia/nemotron-3.5-super-vl-120b-a12b`) through the NVIDIA Build API: tool calling, schema-enforced JSON, image and video input, a reasoning budget per call. Alongside it, **Nemotron 3.5 Embed** (`nvidia/nemotron-3.5-embed-1b`) on NVIDIA's hosted GPUs, for meaning across languages.

## What it does

The Puppet Post is a live newspaper about influence operations. It reads all of Bluesky, the Telegram channels and Mastodon servers of the Pravda network, and the network's own news sites. Our detector finds coordination; **Nemotron explains it, judges it, and answers people**, in nine jobs:

1. **Investigator agent.** For each suspicious group of accounts it reads the posts, anonymised profiles and our measurements, looks at full-size images and up to two minutes of video, and calls tools: `fetch_page`, `domain_info` (registration age, propaganda lists) and `search_news`, which checks each claim against independent outlets in the right national editions (36, e.g. Belgium in Dutch and French). It names manipulation techniques with exact quotes.
2. **Real-or-staged judge.** It writes the strongest innocent explanation first, then judges *staged*, *organic* or *insufficient evidence*.
3. **Campaign analyst (long context).** It reads all groups that share accounts at once: 40 alerts turned out to be one scam template run by 25 accounts.
4. **Story analyst.** Every 15 minutes it compares how Russian state media, the Pravda network's own sites (Finland, Sweden, Estonia, Latvia, Germany, English) and its Telegram channels tell an event against independent outlets, pictures included: contradictions, claims only state media make, and how a claim is laundered from Telegram into Finnish, Estonian and Latvian.
5. **Look for the Truth (for everyone).** Paste any post or article: Nemotron reads it with the same tools and our live network data and explains, in plain words, who is behind it, what independent newspapers say, and how its words try to persuade.
6. **Ask the Truth.** An agent that answers questions like *"Is the war between Ukraine and Russia ending?"* with read-only tools over our data plus the news search, citing every source.
7. **Picture checker:** when a picture returns with a new caption, it says which caption the picture fits.
8. **Red team:** it writes fictional propaganda campaigns at three difficulty levels to measure our own recall. Nothing is ever posted.
9. **Translated swarms with Nemotron Embed:** posts on our topics are embedded; the same claim in several languages from different accounts becomes a group. In its first live hour it found 7, e.g. *"The US will reduce troops in Europe…"* pushed in English and Russian by 5 accounts, which the investigator judged staged.

## Why it matters

Counting copies finds coordination, but people need to know *what* is pushed, *by whom*, *whether it is real*, and *where the evidence is*, in their own language. That needs exactly Nemotron's strengths: tool-using agents, reasoning, fine visual detail, video, many languages and long context. Without Nemotron The Puppet Post is an alarm; with it, every alarm, every story and every link a reader pastes comes with a report anyone can check.

## How we improved its outputs

**Grounding by construction.** Every statement cites evidence ids it was shown; technique quotes must appear word for word; anything else is removed and counted. On a fixed set of 12 live clusters, claims backed by evidence rose from **91% to 98%** (prompt v3 → v5) and organic controls called staged fell from **1 of 3 to 0**.

**21 changes, each logged with its observation and measured effect** (`docs/IMPROVEMENTS.md`). Examples:
- A JSON list looped to the token limit → every list capped in the schema.
- Labels came before reasons → reasons first in the schema.
- It once cleared accounts on a documented propaganda server → **a rule overrides the model**: it may abstain, never clear.
- A Belgian article got *no coverage*: it read a consent page, searched only the Netherlands and used 8-word queries → national editions, short queries retried with fewer words, the original site kept as the source. Same article: 4 outlets found, claims confirmed.
- Story picks ignored the Pravda sites' Finnish, Estonian and Latvian items → one story per round from the sites around Finland; first round showed one claim retold in three languages.
- We blamed NVIDIA for rejecting overlapping requests; **a direct test disproved it** (the limit is the rate) → 3 parallel calls, a fair queue, and people waiting get priority over background work (answers 148 s → 35 s).
- **A measured negative:** we tried Nemotron Embed against our evasive test campaigns. Their posts tell one story from different angles, so meaning does not link them (0 of 4), while it would have linked ordinary news a thousand times an hour. We did not ship it for that; we shipped it for translated claims, where it works.

**Honest limits:** asking the same prompt twice changed 4 of 11 verdicts, so we treat verdicts as leads for people, and report grounding and the rules as the solid gains. Reviewer decisions feed back into the prompt as calibration examples.
