# Demo video script (2:00 maximum)

Black-and-white studio intro, then a screen recording of the live site: https://thepuppetpost.tech/

## Before recording

- **Leave the reviewer edition** (masthead, top right: "Leave"). Record as a public visitor: reviewer mode shows posts and pictures of individual accounts.
- **Pre-run** the Look for the Truth link and the Ask the Truth question just before recording, and cut every wait in editing.
- **Random reviews first:** the false-positive rate on the Accuracy page shows "Not yet" until reviewers have drawn the random sample.
- Browser full screen, zoom 110–125%, no bookmarks bar. Show the address bar once.
- About 280 spoken words fit in 2 minutes: speak briskly, scroll while you talk, and keep the fanfare and spinning page to 3 seconds.
- Read figures off the screen only; they are live and change. The one fixed figure is the grounding test (91% → 98%, `docs/IMPROVEMENTS.md`).
- Still too long? Cut Ask the Truth first, then the joke.

## Script

### 1. Studio

*Show:* the studio, the intro and the joke, then the fanfare and the front page spinning in.

*Say:* "Have you ever wondered which information can be trusted? …" (joke) "… Let us show you who's really behind the post."

### 2. Front Page

*Show:* the front page, with the address bar visible.

*Say:* "The Puppet Post runs live on a Hostinger VPS: seven Docker services, no GPU. It reads all of Bluesky and the Pravda network's Telegram, Mastodon and news sites. Its brain is NVIDIA Nemotron 3.5 Super VL."

### 3. Contested Stories

*Show:* a story marked **Only state media report it**; scroll through Who says what and Where they contradict.

*Say:* "Every 15 minutes, Nemotron compares state media and the Pravda network with independent outlets it finds through Google News, in 36 national editions: where they contradict, and which claims only state media make."

### 4. Look for the Truth

*Show:* paste a link (e.g. the Pravda-site article from that story) and scroll through the report.

*Say:* "Paste any link. Nemotron, as an agent with tools, reads the text, pictures and video, fetches the page, checks the domain's age, and searches Google News for each claim. It quotes every persuasion technique word for word, like fear appeals. When the evidence is thin, it says so."

### 5. Ask the Truth (cut first)

*Show:* a pre-run answer.

*Say:* "Or just ask: every answer cites its sources."

### 6. The Network

*Show:* The Network, then open one group: its **Signals**, then the **Nemotron investigation**.

*Say:* "Underneath, four thousand posts a minute flow through Aiven Kafka; after a deploy the detector rewinds an hour, so it never goes blind. It links accounts by MinHash near-duplicate text, shared links, repost rings, and the same claim across languages with Nemotron Embed. Every signal shows its value and threshold; coordination alone is never enough. Nemotron investigates each group, and a judge argues the innocent explanation first: real crowd or staged? Aiven PostgreSQL keeps it all."

### 7. Accuracy & Corrections

*Show:* the figures at the top of the page.

*Say:* "We measure ourselves, live: precision from human review, false positives on a random sample, and fake campaigns Nemotron wrote to test us. Copy-paste and reworded ones we catch; evasive ones not yet, and we say so. Every claim must cite evidence or it is removed: grounded claims rose from 91 to 98 percent."

### 8. Close

*Show:* the front page.

*Say:* "A person always decides. See who's really behind the post."

## What each judge hears

- **Hostinger:** live on a Hostinger VPS (seven services, no GPU); groups, not single posts; the signals by name (MinHash near-duplicates, shared links, repost rings, translated claims) with values and thresholds; false positives (coordination alone is never enough, the innocent explanation first, "not enough evidence"); precision and false-positive rate, with our blind spot (evasive campaigns) said out loud.
- **NVIDIA:** Nemotron 3.5 Super VL by name, plus Nemotron Embed; an agent with tools (page fetch, domain age, Google News in 36 editions), pictures and video; stories, Look for the Truth, investigation, the real-or-staged judge, the red team; improved by grounding, measured (91% → 98% cited claims). Details: `docs/submissions/nvidia.md`.
- **Aiven:** Kafka carries every post and lets the detector rewind an hour after each deploy; PostgreSQL keeps the groups, reports and reviews. Details: `docs/submissions/aiven.md`.
