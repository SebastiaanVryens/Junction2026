# Decision log

Newest first. Each entry: what we decided, why, and who decided. Proposals waiting for approval live in [`PLAN.md`](PLAN.md) §2, not here.

## 2026-11-14: 72-hour deletion also covers names and investigation evidence
- **Gap found in the ops check:** after 72 hours we removed account ids and post texts, but kept account handles, display names and bios, and the post ids (which contain account ids) inside stored investigations.
- **Decision:** these are now removed after 72 hours too. Investigations keep the report itself; reviews and aggregate statistics stay. Dry run on the live database (rolled back): nothing is older than 72 hours yet.
- **Still on a laptop:** the local stream recordings (`data/recordings/`, git-ignored) hold raw ids and are used for the evaluation replays. Delete them after the event.
- **Decided by:** Claude, as the CLAUDE.md §7 rule requires; flagged here for review.

## 2026-11-14: Mastodon source (the servers on IFTAS's list)
- **Decision:** poll the public local timelines of the 78 Mastodon servers on IFTAS's list every 5 minutes (no login; 59 answered on 14 Nov, about 500 posts/hour in total). Bluesky has suspended 2,508 of the 2,850 listed accounts, but their servers keep running and new accounts appear there (e.g. two-week-old accounts with random-letter names and 1,000+ posts each), so this source sees the network where Bluesky no longer does.
- **One identity per person:** a Mastodon account that is bridged and still active on Bluesky is skipped, because Jetstream already has its posts. The detector also drops the second copy of any post by its original URL (Bridgy Fed records it). First live round: 65 of 412 posts skipped as already on Bluesky.
- **Profiles** come from each account's own server, and its `bot` flag counts as declared automation. Privacy is the same as for Bluesky: keyed-hash IDs, 72-hour retention, and names shown publicly only for accounts on IFTAS's public list.
- **Found while building it:** Bridgy Fed writes `_` in usernames as `-`. Our first lookup missed this, so the IFTAS numbers are corrected to 2,508 suspended / 241 not on Bluesky / 101 active (were 2,333 / 423 / 94).
- **Decided by:** team (Sebastiaan) chose Mastodon as a source; the scope and the identity rule are Claude's, flagged here for review.

## 2026-11-14: Nemotron checks independent news coverage
- **Decision:** the investigator gets a `search_news` tool (Google News search feeds, no key, at most one request every 2 s, results cached). Nemotron chooses the query and the edition for each claim. The team asked for this, pointing out that official news channels can themselves be the propaganda.
- **Google News finds articles; it does not vouch for them.** Every outlet in the results is checked against the propaganda-domain lists. The tool reports the number of distinct outlets, and the prompt tells Nemotron to judge independence by distinct outlets in different countries, never by size or "official" status, and to search each side's edition when a claim is contested between countries.
- **Editions:** 24, each tested on 14 Nov. Chosen to cover the languages the Pravda network targets around us (its subdomains include Finland, Sweden, the Baltic states, Czechia and Slovakia), plus English. Danish has no edition, and the Russian edition mostly returns Russian-language Ukrainian outlets.
- **Not used:** GDELT (rate-limited us with HTTP 429).
- **Decided by:** team (Sebastiaan) asked for the check and the country focus; the source and safeguards are Claude's, flagged here for review.

## 2026-11-14: M3 design choices (Claude, for team review)
- **Jobs 1 and 2 in one investigation:** the report contains the investigator's findings *and* the real-or-staged verdict, so each cluster costs 2–4 model calls instead of two separate pipelines.
- **The model does not see FactsLens's tier or score,** only the raw measurements, so its verdict is an independent second opinion that we can compare with ours (and with reviewers) in M4.
- **Account handles are not sent to NVIDIA;** accounts appear as A1, A2… with age, followers, bio and Mastodon server.
- **Own call budget** (20/min, 300/h) below the free tier's reported ~40/min per key; one key in use until key pooling is confirmed allowed.
- **Linked pages are fetched by our server** with a guard against internal addresses (SSRF), size and type limits.
- **Video goes to the model as a short re-encoded clip** (first 20 s, 2 fps, 320 px), since the API accepts video directly.

## 2026-11-14: M2 design choices (Claude, for team review)
- **Public vs reviewer view:** the public sees each cluster's shared message, signals and aggregate account statistics; account names and individual posts need the reviewer token (`REVIEWER_TOKEN` in `.env`). Follows CLAUDE.md §7 ("show content only when the reviewer needs it").
- **Coordination alone is not suspicious:** a share button's pre-filled headline coordinates people too. "Suspicious" needs account or provenance evidence on top of coordination.
- **Disclosed bots get their own tier** ("declared automation") instead of "suspicious"; provenance evidence overrides it.
- **Hosting:** the VPS template ("Ubuntu 24.04 with Docker and Traefik") already runs Traefik on 80/443, so the app registers with it via labels instead of shipping Caddy.
- **Jetstream:** all four public servers are configured; the ingest rotates on stalls (both us-east servers stalled at the same stream position on 14 Nov).

## 2026-11-14: M1 started; sources are pluggable; Telegram and Pravda added
- **Decision:** start M1 with D1–D9 as written in PLAN.md §2. Every data source implements one `Source` interface and produces the same `Event` record, so platforms plug into one pipeline. Bluesky is the live stream; **Telegram channel pages and the Pravda sites are added as upstream sources** (to trace laundered content back to where it came from).
- **Other platforms (checked 14 Nov, details in CLAUDE.md §9):** X is pay-per-use ($0.005 per post read), Reddit blocks unauthenticated access, Instagram has no official way to read arbitrary public posts, YouTube's API allows 100 searches/day. None are added now. No scraping around platform blocks.
- **Recording:** a local recorder saves the stream to `data/recordings/` (git-ignored) because Jetstream only replays about 36 hours. Recordings and the 24 h Kafka topic hold raw account IDs; keyed hashing applies to what we keep in PostgreSQL. Delete the recordings after the event.
- **Deploy detail (D1):** `infra/deploy.sh` will copy the committed tree over SSH and build on the VPS, so the VPS needs no GitHub credentials.
- **Decided by:** team (Sebastiaan); the recording and deploy details are Claude's, flagged here for review.

## 2026-11-13: No `.env` template in the repo
- **Decision:** each person creates their own `.env`; the repo has no `.env.example`, and the README doesn't explain the settings.
- **Decided by:** team (Sebastiaan).

## 2026-11-13: Project name
- **Decision:** the project is called **FactsLens**, tagline *"See who's really behind the post."* The Python package is `factslens`.
- **Why the tagline:** it echoes Hostinger's "Trust the source" wording ("who is really behind what they're reading") and makes clear we detect coordination, not just fact-check claims.
- **Decided by:** team (Sebastiaan) chose the name; the tagline is Claude's suggestion, easy to swap.

## 2026-11-13: Milestone hand-off
- **Decision:** after each milestone Claude summarises what works, what doesn't and the next step, then continues without waiting. Anything that costs money or is hard to undo still needs the team's OK. M1 starts only after the team approves D1–D9 in PLAN.md §2.
- **Decided by:** team (Sebastiaan).

## 2026-11-13: Service stack
- **Decision:**
  - **Must-haves:** the Hostinger VPS (app runs there) and NVIDIA Build (Nemotron 3.5 Super VL).
  - **Kept for the Aiven prize and real work:** Aiven PostgreSQL + Kafka. Kafka is the first thing to cut if time gets tight; Postgres would then move to the VPS.
  - **Not used:** Hostinger AI Router (kept only as a possible backup), Verda (no GPU work left: the simulation runs on CPU, Nemotron is hosted), ClickHouse, Valkey, OpenSearch, Hermes/Oxylabs.
- **Decided by:** team (Sebastiaan).

## 2026-11-13: Nemotron gets four jobs; the demo shows it at every step
- **Decision:** Nemotron 3.5 Super VL does four jobs (PLAN.md §1):
  1. investigator agent (images, video, websites, domain age, techniques, provenance);
  2. false-positive judge (real crowd vs staged);
  3. red-team generator of synthetic test campaigns;
  4. audience modelling for the spread forecast.

  Plus a feedback loop from reviewer decisions into the prompts.
- **Why:** NVIDIA scores whether Nemotron is central and whether its output is measured and improved. Each job has its own number on the metrics page.
- **Consequences:**
  - Jobs 1–3 sit in P0 milestones (M3–M4).
  - Job 4 is P1 (M5), after the metrics are safe.
  - The forecast is calibrated on observed reposts and reported with its measured error.
- **Decided by:** team (Sebastiaan).

## 2026-11-13: Keep Aiven, limited to Kafka + PostgreSQL
- **Decision:** use Aiven for Kafka and PostgreSQL only. ClickHouse and Valkey are not planned.
- **Why:**
  - a separate prize (Best Use of Aiven);
  - frees VPS memory;
  - Kafka enables replay for evaluation and buffers slow Nemotron calls.
- **If time gets tight:** Aiven is the first thing to cut; the main challenge works without it.
- **Decided by:** team (Sebastiaan).
