# Demo video script (about 3:00)

Black-and-white studio intro, then a screen recording of the live site: https://srv2159312.hstgr.cloud/

## Before recording

- **Leave the reviewer edition** (masthead, top right: "Leave"). Record as a public visitor: reviewer mode shows posts and pictures of individual accounts.
- **Pre-run** the Look for the Truth link and the Ask the Truth question just before recording, so they open instantly (or cut the wait in editing).
- **Random reviews first:** the false-positive rate on the Accuracy page shows "Not yet" until reviewers have drawn the random sample.
- Browser full screen, zoom 110–125%, no bookmarks bar. Show the address bar at least once.
- Read the figures off the screen; they are live and change. Never say a number that is not on screen.
- About 450 spoken words fit in 3 minutes. Over time? Cut the beats marked *(optional)*.

## Script

| Time | Show | Say | Who it is for |
|---|---|---|---|
| 0:00 | **Studio**: intro and joke, then the fanfare and the spinning front page | "Have you ever wondered which information can be trusted? … Let us show you who's really behind the post." | |
| 0:20 | **Front Page**, address bar visible | "This is The Facts Lens, running live on Hostinger. It reads all of Bluesky, plus the Telegram channels, Mastodon servers and news sites of the Pravda network. The brain behind it is NVIDIA Nemotron 3.5 Super VL. It does nine jobs here; you will see most of them." | Hostinger, NVIDIA |
| 0:35 | A story marked **Only state media report it**: Who says what → Where they contradict → Claims only state media make | "Every 15 minutes, Nemotron compares how Russian state media, the Pravda sites and their Telegram channels tell a story with independent newspapers in 36 national editions of Google News. Where they contradict, and which claims only state media make. Every line is footnoted." | NVIDIA: story analyst |
| 0:55 | **Look for the Truth**: paste a link (e.g. the Pravda-site article from that story), scroll the report | "Anyone can paste a link: a post or an article from anywhere. Nemotron reads the text, the pictures and the video, and uses tools: it opens the website, checks the domain's age and the propaganda lists, and searches independent news. It tells you who is behind it, whether a swarm is pushing it, and how the words persuade: loaded language, fear appeals, whataboutism, fabricated authority. When the evidence is thin, it says so." | NVIDIA: investigator; Hostinger: name the technique, trust the source |
| 1:25 | **Ask the Truth**: e.g. "Who is pushing the story that Finland was dragged into NATO?" | "Or just ask. A Nemotron agent searches our own data and the news, and every sentence cites its source." | NVIDIA: agent with tools |
| 1:35 | Type **/pipeline**: Service status, events waiting in Kafka | "Underneath: around four thousand posts a minute from three platforms flow into one Aiven Kafka stream; this is it, live. A restarted service rewinds an hour in Kafka and loses nothing. Aiven PostgreSQL is the shared memory of our seven services: the groups, Nemotron's reports, the reviews. It deletes raw data after 72 hours." | Aiven |
| 2:00 | **The Network** → On our topics → open one group: **Signals**, **Nemotron investigation**, the second opinion | "We don't judge single posts: we find groups of accounts acting together. Every group shows which signals fired, with the value and the threshold: posting in sync, copied text, brand-new accounts, and, with Nemotron Embed, the same claim in several languages. Nemotron then investigates the group's images, videos and websites and writes the evidence. A second opinion asks: real crowd or staged? It argues the innocent explanation first, because breaking news makes real people post alike." | Hostinger: explainable, false positives; NVIDIA: judge, Embed |
| 2:30 | *(optional)* **The Network** → The campaigns: open one | "Nemotron also reads all groups that share accounts at once, and turns dozens of alerts into one campaign." | NVIDIA: long context |
| 2:35 | **Accuracy & Corrections**: the figures | "We measure ourselves, live: flags confirmed by reviewers, our false-positive rate on a random sample, the documented Pravda accounts we found, and fake campaigns Nemotron wrote to test us, even ones built to evade us. We improved Nemotron too: every claim must cite evidence it was shown, every quote must match word for word, and reviewers' decisions flow back into its prompt." | Hostinger: precision and FPR; NVIDIA: red team, output quality |
| 2:55 | Front Page (or back to the studio) | "The Facts Lens never accuses anyone automatically: a person always decides. See who's really behind the post." | |

## Points the judges must hear

- **Hostinger:** live on Hostinger at a public URL; groups, not single posts; signals with values and thresholds (explainable); false positives handled (the second opinion, "not enough evidence", breaking-news controls); precision and false-positive rate shown with sample sizes.
- **NVIDIA:** the model's name (Nemotron 3.5 Super VL) said out loud; what it does (stories, Look for the Truth, Ask, investigator, real-or-staged judge, campaigns, red team, plus Nemotron Embed for translated claims); why it matters (pictures, video, tools, many languages); how we improved it (cited evidence, exact quotes, the reviewer feedback loop). Details: `docs/submissions/nvidia.md`.
- **Aiven:** Kafka carries every post from three platforms to the detector and survives restarts; PostgreSQL is the shared state of seven services, the review feedback loop and the 72-hour deletion. Details: `docs/submissions/aiven.md`.
