# Demo video script (about 2:45)

Black-and-white studio intro, then a screen recording of the live site: https://srv2159312.hstgr.cloud/

## Before recording

- **Leave the reviewer edition** (masthead, top right: "Leave"). Record as a public visitor: reviewer mode shows posts and pictures of individual accounts.
- **Pre-run** the Look for the Truth link and the Ask the Truth question just before recording, so they open instantly (or cut the wait in editing).
- **Random reviews first:** the false-positive rate on the Accuracy page shows "Not yet" until reviewers have drawn the random sample.
- Browser full screen, zoom 110–125%, no bookmarks bar. Show the address bar at least once.
- Read the figures off the screen; they are live and change. Never say a number that is not on screen.

## Script

| Time | Show | Say |
|---|---|---|
| 0:00 | Studio: intro and joke, then the fanfare and the spinning front page | "Have you ever wondered which information can be trusted? … Let us show you who's really behind the post." |
| 0:20 | **Front Page**, address bar visible | "This is The Facts Lens, running live on Hostinger. It reads Bluesky, Mastodon, Telegram and the Pravda network's own sites as it happens. Every 15 minutes, NVIDIA Nemotron compares their stories with independent newspapers in 36 national editions of Google News." |
| 0:35 | Open a story labelled **Only state media report it**; scroll through Who says what → Where they contradict → Claims only state media make | "Each story shows who says what, where they contradict each other, and which claims only state media make. Every line is footnoted to its source." |
| 0:55 | **Look for the Truth**: paste a link (e.g. the Pravda-site article from that story), open the report | "Paste any link: a post or an article from anywhere. Nemotron 3.5 Super VL reads the text, the pictures and the video, checks the website and the account, and searches independent news." |
| 1:10 | Scroll: Who is behind it → Seen in our network → The claims, checked → How it tries to persuade | "It tells you who is behind it, whether a swarm of accounts is pushing it, what independent newspapers say, and how the words try to persuade you, in plain language: loaded language, fear appeals, whataboutism, fabricated authority. If the evidence is thin, it says so instead of guessing." |
| 1:25 | **Ask the Truth**: ask e.g. "Who is pushing the story that Finland was dragged into NATO?" | "You can also just ask. Nemotron searches our own data and the news, and every sentence in the answer links to its source." |
| 1:40 | **The Network**: On our topics, then open one swarm | "Underneath, every post flows through Aiven Kafka into our detector, and Aiven PostgreSQL keeps the groups, the reports and the reviews. We don't judge single posts: we find groups of accounts acting together." |
| 1:55 | On the group page: **Signals** (values and thresholds), **Nemotron investigation**, the second opinion | "Every group shows which signals fired, with the value and the threshold: posting in sync, copied text, brand-new accounts, the same claim translated into several languages. Then Nemotron writes the evidence report, and a second opinion asks: real crowd or staged? Breaking news makes real people post alike, and that is where false alarms come from." |
| 2:15 | **Accuracy & Corrections**: the figures, then Real crowd or staged?, then Where we are blind | "We measure ourselves, live. How many flags reviewers confirmed, our false-positive rate on a random sample, how many accounts of a documented Pravda network we found, and how many fake campaigns Nemotron wrote to test us, including ones made to avoid detection. Every figure says what it counts and how many, and we show where we are blind." |
| 2:35 | Back to the Front Page (or the studio) | "The Facts Lens never accuses anyone automatically: every finding goes to a human reviewer. See who's really behind the post." |

## Points the judges must hear

- **Hostinger:** live on Hostinger at a public URL; groups, not single posts; signals with values and thresholds (explainable); false positives handled (second opinion, "not enough evidence", organic breaking-news controls); precision and false-positive rate shown with sample sizes.
- **NVIDIA:** Nemotron 3.5 Super VL reads text, pictures, video and websites (Look for the Truth, the group reports); judges real crowd or staged; writes our test campaigns; compares the stories; answers questions with sources. Nemotron 3.5 Embed finds the same claim across languages.
- **Aiven:** Kafka carries every post from the live feeds to the detector and investigator; PostgreSQL holds the groups, reports, reviews and the live figures.
