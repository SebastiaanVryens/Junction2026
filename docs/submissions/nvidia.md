# FactsLens: NVIDIA "Best Use of Nemotron"

*Draft for the submission form (text field). Live: https://srv2159312.hstgr.cloud/*

## Model used

**NVIDIA Nemotron 3.5 Super VL** (`nvidia/nemotron-3.5-super-vl-120b-a12b`), through the NVIDIA Build API, with tool calling, schema-enforced JSON output, image and video input, and a reasoning budget per call.

## What it does

FactsLens watches public Bluesky, Telegram and Mastodon activity live and finds swarms: groups of accounts acting together. Our detector finds the coordination; **Nemotron explains it**, in six jobs:

1. **Investigator agent.** For each suspicious cluster it reads the posts, anonymised account profiles and our measurements, looks at full-size images and up to two minutes of video, and calls tools: `fetch_page`, `domain_info` (registration age, propaganda lists) and `search_news`, which checks each factual claim against independent outlets in the relevant languages (24 news editions). It names manipulation techniques with exact quotes.
2. **Real-or-staged judge.** In the same report it writes the strongest innocent explanation first, then judges *staged*, *organic* or *insufficient evidence*.
3. **Campaign analyst (long context).** It reads every cluster that shares accounts at once and says whether they are one operation and which accounts to review first. Live example: 40 alerts turned out to be one begging-scam template run by 25 accounts.
4. **Story analyst.** Every 15 minutes it compares how Russian state media (TASS, RT, Lenta.ru, RBC mirrors), Telegram channels the Pravda network republishes, and independent outlets tell the same event, including the photos. It reports contradictions, claims only state media make, and which of our clusters push which version. It does not decide who is right.
5. **Picture checker.** When the same picture is posted again with a different caption, it says which caption the picture fits, from visible clues (it reads station names and temperatures off weather charts, for example).
6. **Red team.** It writes synthetic propaganda campaigns about a fictional country at three difficulty levels, which we replay into recorded traffic to measure our own recall. Nothing is ever posted.

It also translates posts for reviewers, keeping loaded words.

## Why it matters

Counting copies finds coordination, but a moderator needs to know *what* is pushed, *how*, *whether it is real*, and *where the evidence is*. These tasks need exactly what NVIDIA lists as the model's strengths: tool-using agents, reasoning, fine visual detail, video, many languages and long context. Without Nemotron FactsLens is an alarm; with it, every alarm comes with a report a human can check in a minute.

## How we improved its outputs

**Grounding by construction.** Every statement must cite evidence ids it was shown (posts, accounts, images, pages, searches, articles). Technique quotes must appear verbatim in the cited post, and anything else is removed automatically and counted. A *staged* verdict without a grounded claim is downgraded.

**Measured on a fixed evaluation set** of 12 live clusters (documented propaganda servers, spam networks, three organic controls), with old prompt versions re-run from git. Per version:
- claims backed by evidence: **91% → 98%** (v3 → v5);
- organic controls wrongly called staged: **1 of 3 → 0**.

**Changes driven by what we saw, each logged with its effect** (`docs/IMPROVEMENTS.md`, 11 entries):
- The model looped inside a JSON list until the token limit. We capped every list in the schema, so constrained decoding must close it.
- It picked labels before reasoning ("insufficient evidence, *high* confidence" above reasons that all said staged). We moved the reasons before the label in the schema and defined "staged" to include spam and scam networks.
- One label for posts with several claims got the news check wrong. Now there is one check per claim, with labels that say what they mean (*real event, distorted*).
- It once called random-name accounts on a documented propaganda server "organic". **A rule now overrides the model:** it may not clear a cluster with documented-network evidence, only abstain.
- **Media, measured:** full-size images cost about 300 extra tokens each and let it read screenshots. A 90-second video costs the same tokens as 20 seconds (the API samples a fixed number of frames), so it now sees the whole clip.
- **Feedback loop:** human reviewers' decisions (with type: propaganda, bots, spam) become calibration examples in the prompt.

**What we learned honestly:** running the same prompt version twice changed 4 of 11 verdicts. With one run per version, verdict differences between versions are within chance, so we report grounding and the rules (which do not depend on chance) as the solid gains, and verdict comparisons as needing more runs and human labels.
