"""Investigator service (Nemotron jobs 1 and 2): investigate the most suspicious clusters.

    python -m factslens.investigator [--once CLUSTER_ID]

Picks clusters rated suspicious or watch, highest score first; re-investigates a cluster only
when it grew by half since the last report. Stays inside the call budget in settings
(NEMOTRON_MAX_CALLS_PER_MINUTE / _PER_HOUR); when the hourly budget is used up it waits.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from factslens.common import db
from factslens.common.logging import configure_logging
from factslens.common.settings import get_settings
from factslens.enrich.lists import KnownLists
from factslens.investigator import campaigns, recycled, stories
from factslens.investigator.agent import PROMPT_VERSION, InvestigationResult, Investigator
from factslens.investigator.evidence import EvidenceGatherer
from factslens.investigator.llm import BudgetExhausted, CallRecord, ChatClient

log = logging.getLogger("factslens.investigator")

SERVICE = "investigator"
IDLE_SECONDS = 15.0
BETWEEN_INVESTIGATIONS_SECONDS = 5.0
BUDGET_WAIT_SECONDS = 300.0
CAMPAIGN_EVERY_SECONDS = 900.0  # one campaign report per round at most
CAMPAIGN_FIRST_AFTER_SECONDS = 60.0
STORY_EVERY_SECONDS = 900.0  # one round: at most 2 stories, ~3-5 model calls (team choice, 14 Nov)
STORY_FIRST_AFTER_SECONDS = 180.0
STORY_TELEGRAM_CHANNELS = 8
RECYCLED_EVERY_SECONDS = 300.0
HASH_BATCH = 100  # images hashed per round
JUDGE_BATCH = 3  # reuse pairs judged by Nemotron per round

HASH_TODO = """
SELECT DISTINCT ON (p.post_id, m->>'ref') p.post_id, m->>'ref' AS media_ref, p.cluster_id, p.account_hash,
       p.posted_at, left(p.text, 600) AS caption, a.handle
FROM cluster_posts p
CROSS JOIN LATERAL jsonb_array_elements(p.media_refs) m
LEFT JOIN accounts a ON a.account_hash = p.account_hash
WHERE m->>'kind' = 'image'
  AND NOT EXISTS (SELECT 1 FROM image_hashes h WHERE h.post_id = p.post_id AND h.media_ref = m->>'ref')
ORDER BY p.post_id, m->>'ref', p.posted_at DESC
LIMIT %s
"""

SAVE_HASH = """
INSERT INTO image_hashes (post_id, media_ref, cluster_id, account_hash, posted_at, caption, source, hash)
VALUES (%(post_id)s, %(media_ref)s, %(cluster_id)s, %(account_hash)s, %(posted_at)s, %(caption)s, %(source)s,
        %(hash)s)
ON CONFLICT DO NOTHING
"""

REUSE_CANDIDATES = """
SELECT a.post_id AS a_post, a.media_ref AS a_ref, a.cluster_id AS a_cluster, a.account_hash AS a_account,
       a.posted_at AS a_at, a.caption AS a_caption, a.source AS a_source,
       b.post_id AS b_post, b.media_ref AS b_ref, b.cluster_id AS b_cluster, b.account_hash AS b_account,
       b.posted_at AS b_at, b.caption AS b_caption, b.source AS b_source,
       bit_count((a.hash # b.hash)::bit(64)) AS distance
FROM image_hashes b
JOIN image_hashes a ON a.hash IS NOT NULL AND a.posted_at < b.posted_at AND a.account_hash <> b.account_hash
     AND bit_count((a.hash # b.hash)::bit(64)) <= %(max_distance)s
WHERE b.hash IS NOT NULL AND b.created_at > now() - interval '3 hours'
  AND NOT EXISTS (SELECT 1 FROM image_reuse r WHERE r.later_post = b.post_id)
ORDER BY b.posted_at DESC, a.posted_at
LIMIT 200
"""

SAVE_REUSE = """
INSERT INTO image_reuse (media_ref, hash_distance, earlier_post, earlier_cluster, earlier_at, earlier_caption,
                         earlier_source, later_post, later_cluster, later_at, later_caption, later_source,
                         status,
                         supports, judgement, model, prompt_version)
VALUES (%(media_ref)s, %(distance)s, %(a_post)s, %(a_cluster)s, %(a_at)s, %(a_caption)s, %(a_source)s,
        %(b_post)s, %(b_cluster)s, %(b_at)s, %(b_caption)s, %(b_source)s, %(status)s, %(supports)s,
        %(judgement)s, %(model)s, %(prompt_version)s)
ON CONFLICT DO NOTHING
"""

RECENT_STORIES = (
    "SELECT title FROM stories WHERE status = 'done' AND created_at > now() - interval '24 hours'"
)

STORY_CLUSTERS = """
SELECT c.id, c.kind, c.label, coalesce(c.tier, 'pending') AS tier, coalesce(c.score, 0) AS score, c.accounts,
       (SELECT i.verdict FROM investigations i WHERE i.cluster_id = c.id AND i.status = 'done'
        ORDER BY i.created_at DESC LIMIT 1) AS verdict,
       (SELECT array_agg(left(p.text, 200)) FROM (
            SELECT text FROM cluster_posts p WHERE p.cluster_id = c.id AND p.text IS NOT NULL
            ORDER BY (p.text ILIKE ANY(%(patterns)s)) DESC, p.posted_at LIMIT 3) p) AS samples
FROM clusters c
WHERE c.last_seen > now() - interval '24 hours'
  AND (c.label ILIKE ANY(%(patterns)s)
       OR EXISTS (SELECT 1 FROM cluster_posts p WHERE p.cluster_id = c.id AND p.text ILIKE ANY(%(patterns)s)))
ORDER BY coalesce(c.score, 0) DESC, c.accounts DESC
LIMIT 5
"""

STORY_MEDIA = """
SELECT post_id, media_refs FROM cluster_posts
WHERE cluster_id = ANY(%s) AND media_refs::text <> '[]'
ORDER BY posted_at LIMIT 10
"""

SAVE_STORY = """
INSERT INTO stories (status, title, story_status, cluster_ids, report, evidence, model, prompt_version, calls,
                     prompt_tokens, completion_tokens, latency_ms, error)
VALUES (%(status)s, %(title)s, %(story_status)s, %(cluster_ids)s, %(report)s, %(evidence)s, %(model)s,
        %(prompt_version)s, %(calls)s, %(prompt_tokens)s, %(completion_tokens)s, %(latency_ms)s, %(error)s)
"""

CAMPAIGN_CANDIDATES = """
SELECT id, coalesce(score, 0) AS score FROM clusters
WHERE tier IN ('suspicious', 'watch') AND last_seen > now() - interval '24 hours'
"""

CAMPAIGN_LINKS = """
SELECT m.cluster_id AS a, o.cluster_id AS b, count(*) AS n
FROM cluster_members m
JOIN cluster_members o ON o.account_hash = m.account_hash AND o.cluster_id < m.cluster_id
WHERE m.cluster_id = ANY(%(ids)s) AND o.cluster_id = ANY(%(ids)s)
GROUP BY 1, 2 HAVING count(*) >= 2
"""

LAST_CAMPAIGN = """
SELECT status, cardinality(cluster_ids) AS clusters, created_at < now() - interval '20 minutes' AS stale
FROM campaigns WHERE campaign_key = %s ORDER BY created_at DESC LIMIT 1
"""

CAMPAIGN_MEMBERS = """
SELECT m.cluster_id, m.account_hash, a.handle, a.created_at, a.followers, a.follows, a.posts,
       a.description, a.status, coalesce(a.on_public_list, false) AS on_public_list
FROM cluster_members m LEFT JOIN accounts a ON a.account_hash = m.account_hash
WHERE m.cluster_id = ANY(%s)
"""

CAMPAIGN_FINDINGS = """
SELECT DISTINCT ON (cluster_id) cluster_id, report FROM investigations
WHERE cluster_id = ANY(%s) AND status = 'done' ORDER BY cluster_id, created_at DESC
"""

SAVE_CAMPAIGN = """
INSERT INTO campaigns (campaign_key, cluster_ids, accounts, status, title, kind, verdict, report, evidence,
                       model, prompt_version, calls, prompt_tokens, completion_tokens, latency_ms, error)
VALUES (%(key)s, %(cluster_ids)s, %(accounts)s, %(status)s, %(title)s, %(kind)s, %(verdict)s, %(report)s,
        %(evidence)s, %(model)s, %(prompt_version)s, %(calls)s, %(prompt_tokens)s, %(completion_tokens)s,
        %(latency_ms)s, %(error)s)
"""

type Connection = psycopg.Connection[dict[str, Any]]

NEXT_CLUSTER = """
SELECT c.* FROM clusters c
LEFT JOIN LATERAL (
    SELECT accounts_at, created_at, status FROM investigations i
    WHERE i.cluster_id = c.id ORDER BY created_at DESC LIMIT 1
) last ON true
WHERE c.tier IN ('suspicious', 'watch')
  AND c.last_seen > now() - interval '6 hours'
  AND (last.created_at IS NULL
       OR (last.status = 'failed' AND last.created_at < now() - interval '20 minutes')
       OR (c.accounts >= greatest(last.accounts_at * 1.5, last.accounts_at + 3)
           AND last.created_at < now() - interval '30 minutes'))
ORDER BY c.score DESC NULLS LAST, c.accounts DESC
LIMIT 1
"""

REVIEW_EXAMPLES = """
SELECT c.kind, c.accounts, c.label, r.decision, r.note, r.verdict_at, r.category
FROM reviews r JOIN clusters c ON c.id = r.cluster_id
WHERE r.decision IN ('confirm', 'false_alarm') AND r.cluster_id <> %s
ORDER BY r.created_at DESC
LIMIT 4
"""

LOG_CALL = """
INSERT INTO model_calls (purpose, cluster_id, provider, model, status, attempts, latency_ms,
                         prompt_tokens, completion_tokens, reasoning_tokens, error)
VALUES (%(purpose)s, %(cluster_id)s, %(provider)s, %(model)s, %(status)s, %(attempts)s, %(latency_ms)s,
        %(prompt_tokens)s, %(completion_tokens)s, %(reasoning_tokens)s, %(error)s)
"""

SAVE_INVESTIGATION = """
INSERT INTO investigations (cluster_id, accounts_at, model, prompt_version, status, verdict, report,
                            evidence, calls, prompt_tokens, completion_tokens, latency_ms, error)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def load_inputs(
    connection: Connection, cluster_id: str
) -> tuple[dict[str, Any] | None, list[Any], dict[str, Any]]:
    cluster = connection.execute("SELECT * FROM clusters WHERE id = %s", (cluster_id,)).fetchone()
    posts = connection.execute(
        "SELECT * FROM cluster_posts WHERE cluster_id = %s ORDER BY posted_at", (cluster_id,)
    ).fetchall()
    accounts = {
        row["account_hash"]: row
        for row in connection.execute(
            "SELECT a.* FROM cluster_members m JOIN accounts a USING (account_hash) "
            "WHERE m.cluster_id = %s ORDER BY m.first_seen",
            (cluster_id,),
        ).fetchall()
    }
    return cluster, posts, accounts


def save(
    connection: Connection,
    cluster: dict[str, Any],
    model: str,
    result: InvestigationResult,
    evidence: dict[str, Any],
) -> None:
    report = result.report
    grounding = result.grounding
    evidence = evidence | {
        "tool_calls": result.tool_calls,
        "grounding": None
        if grounding is None
        else {
            "share": round(grounding.share, 3),
            "claims": [grounding.claims_grounded, grounding.claims],
            "techniques": [grounding.techniques_grounded, grounding.techniques],
            "media": [grounding.media_grounded, grounding.media],
            "problems": grounding.problems[:10],
        },
    }
    connection.execute(
        SAVE_INVESTIGATION,
        (
            cluster["id"],
            cluster["accounts"],
            model,
            PROMPT_VERSION,
            "done" if report is not None else "failed",
            report.verdict if report is not None else None,
            Jsonb(report.model_dump()) if report is not None else None,
            Jsonb(evidence),
            result.calls,
            result.prompt_tokens,
            result.completion_tokens,
            result.latency_ms,
            result.error,
        ),
    )


def review_examples(connection: Connection, cluster_id: str) -> list[str]:
    """Human decisions on other clusters, phrased for the prompt (feedback loop)."""
    examples = []
    for row in connection.execute(REVIEW_EXAMPLES, (cluster_id,)).fetchall():
        kind = f", {row['category']}" if row.get("category") else ""
        human = (
            f"coordinated inauthentic (confirmed{kind})" if row["decision"] == "confirm" else "false alarm"
        )
        label = " ".join(row["label"].split())[:140]
        note = f'; reviewer note: "{row["note"][:160]}"' if row.get("note") else ""
        said = f"Nemotron had said {row['verdict_at']}; " if row.get("verdict_at") else ""
        examples.append(
            f"{row['kind']} cluster of {row['accounts']} accounts sharing {label!r}: "
            f"{said}human: {human}{note}"
        )
    return examples


def investigate_one(
    connection: Connection, agent: Investigator, cluster_id: str
) -> InvestigationResult | None:
    cluster, posts, accounts = load_inputs(connection, cluster_id)
    if cluster is None:
        return None
    pack = agent.build_pack(cluster, posts, accounts)
    log.info(
        "investigating",
        extra={"cluster": cluster_id, "refs": len(pack.refs), "media": len(pack.attachments) // 2},
    )
    examples = review_examples(connection, cluster_id)
    result = agent.investigate(cluster_id, pack, examples)
    save(connection, cluster, agent.chat.model, result, pack.summary | {"feedback_examples": len(examples)})
    log.info(
        "investigation stored",
        extra={
            "cluster": cluster_id,
            "verdict": result.report.verdict if result.report else None,
            "calls": result.calls,
            "latency_ms": result.latency_ms,
            "error": result.error,
        },
    )
    return result


def source_label(handle: str | None) -> str:
    if handle and handle.startswith("telegram:"):
        return f"Telegram channel @{handle.removeprefix('telegram:')}"
    if handle and "@" in handle:
        return f"Mastodon account on {handle.rsplit('@', 1)[1]}"
    return "Bluesky account"


def _did(post_id: str) -> str | None:
    return post_id.removeprefix("at://").split("/")[0] if post_id.startswith("at://") else None


def recycled_round(connection: Connection, chat: ChatClient, gatherer: EvidenceGatherer) -> int:
    """Hash new images, then let Nemotron judge a few new reuses (same picture, other account,
    different caption). Returns the number of judgements stored."""
    for row in connection.execute(HASH_TODO, (HASH_BATCH,)).fetchall():
        data = gatherer.image_bytes(row["media_ref"], _did(row["post_id"]))
        try:
            image_hash = recycled.dhash(data) if data else None
        except (OSError, ValueError):  # not an image Pillow can read
            image_hash = None
        connection.execute(SAVE_HASH, row | {"source": source_label(row["handle"]), "hash": image_hash})
    judged = 0
    seen_later: set[str] = set()
    for pair in connection.execute(
        REUSE_CANDIDATES, {"max_distance": recycled.SAME_IMAGE_DISTANCE}
    ).fetchall():
        if judged >= JUDGE_BATCH or pair["b_post"] in seen_later:
            continue  # one judgement per later post, against its earliest sighting
        earlier = recycled.Sighting(pair["a_post"], pair["a_cluster"], pair["a_account"], pair["a_at"],
                                    pair["a_caption"] or "", pair["a_source"] or "")  # fmt: skip
        later = recycled.Sighting(pair["b_post"], pair["b_cluster"], pair["b_account"], pair["b_at"],
                                  pair["b_caption"] or "", pair["b_source"] or "")  # fmt: skip
        if not recycled.is_reuse(earlier, later, int(pair["distance"])):
            continue
        seen_later.add(later.post_id)
        did = _did(later.post_id)
        image = (
            gatherer.image_from_url(pair["b_ref"])
            if pair["b_ref"].startswith("https://")
            else (gatherer.image_data_url(did, pair["b_ref"]) if did else None)
        )
        verdict = recycled.judge(chat, image, earlier, later)[0] if image else None
        connection.execute(
            SAVE_REUSE,
            pair
            | {
                "media_ref": pair["b_ref"],
                "status": "done" if verdict else "failed",
                "supports": verdict.supports if verdict else None,
                "judgement": Jsonb(verdict.model_dump()) if verdict else None,
                "model": chat.model,
                "prompt_version": recycled.PROMPT_VERSION,
            },
        )
        judged += verdict is not None
    return judged


def keyword_patterns(keywords: list[str]) -> list[str]:
    """ILIKE patterns for keywords of at least 4 characters (shorter ones match too much)."""
    return [f"%{k.strip().replace('%', '').replace('_', '')}%" for k in keywords if len(k.strip()) >= 4][:8]


def story_media(
    gatherer: EvidenceGatherer, pick_headlines: list[stories.Headline], media_rows: list[dict[str, Any]]
) -> list[tuple[str, str, str]]:
    """Up to three photos (Telegram first, then the clusters' posts) and one video from the clusters."""
    media: list[tuple[str, str, str]] = []
    for url in [u for h in pick_headlines for u in h.images]:
        if sum(kind == "image" for _, kind, _ in media) >= stories.MAX_IMAGES:
            break
        if data := gatherer.image_from_url(url):
            media.append((f"I{len(media) + 1}", "image", data))
    video_done = False
    for row in media_rows:
        did = (
            row["post_id"].removeprefix("at://").split("/")[0] if row["post_id"].startswith("at://") else None
        )
        for m in row["media_refs"] or []:
            images = sum(kind == "image" for _, kind, _ in media)
            if m["kind"] == "image" and images < stories.MAX_IMAGES:
                data = (
                    gatherer.image_from_url(m["ref"])
                    if m["ref"].startswith("https://")
                    else (gatherer.image_data_url(did, m["ref"]) if did else None)
                )
                if data:
                    media.append((f"I{images + 1}", "image", data))
            elif (
                m["kind"] == "video"
                and not video_done
                and did
                and (data := gatherer.video_data_url(did, m["ref"]))
            ):
                media.append(("V1", "video", data))
                video_done = True
    return media


def story_round(
    connection: Connection, chat: ChatClient, gatherer: EvidenceGatherer, lists: KnownLists
) -> int:
    """Pick up to two fresh state-media stories and compare how everyone tells them."""
    import httpx

    from factslens.ingest.telegram import pravda_source_channels

    settings = get_settings()
    now = stories.now_utc()
    channels = pravda_source_channels(
        settings.resolved(settings.ground_truth_dir) / "telegram_channels.csv", STORY_TELEGRAM_CHANNELS
    )
    with httpx.Client(timeout=15, headers={"User-Agent": stories.USER_AGENT}) as client:
        headlines = stories.fetch_state_media(client, now) + stories.fetch_telegram(client, channels, now)
    covered = [r["title"] for r in connection.execute(RECENT_STORIES).fetchall()]
    picks = stories.pick_stories(chat, headlines, covered)
    log.info("story round", extra={"headlines": len(headlines), "picked": [p.title for p in picks]})
    by_ref = {f"H{n}": h for n, h in enumerate(headlines, 1)}
    done = 0
    for pick in picks:
        news = stories.search_coverage(gatherer, pick, lists)
        patterns = keyword_patterns(pick.keywords)
        clusters = connection.execute(STORY_CLUSTERS, {"patterns": patterns}).fetchall() if patterns else []
        media_rows = (
            connection.execute(STORY_MEDIA, ([c["id"] for c in clusters],)).fetchall() if clusters else []
        )
        media = story_media(gatherer, [by_ref[r] for r in pick.refs], media_rows)
        pack = stories.build_story_pack(pick, headlines, news, clusters, media)
        result = stories.compare(chat, pick.title, pack)
        report = result.report
        connection.execute(
            SAVE_STORY,
            {
                "status": "done" if report else "failed",
                "title": report.title if report else pick.title,
                "story_status": report.status if report else None,
                # only clusters that push a version are linked from their cluster page
                "cluster_ids": [
                    pack.evidence["clusters"][a.ref]
                    for a in (report.amplification if report else [])
                    if a.pushes != "unrelated" and a.ref in pack.evidence["clusters"]
                ],
                "report": Jsonb(report.model_dump()) if report else None,
                "evidence": Jsonb(pack.evidence | {"dropped": result.dropped[:20]}),
                "model": chat.model,
                "prompt_version": stories.PROMPT_VERSION,
                "calls": result.calls,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "latency_ms": result.latency_ms,
                "error": result.error,
            },
        )
        log.info("story stored", extra={"title": pick.title, "status": report.status if report else None})
        done += report is not None
    return done


def periodic(name: str, round_function: Any, *args: Any) -> int:
    try:
        return int(round_function(*args))
    except BudgetExhausted:
        raise  # handled by the main loop: wait for the budget
    except Exception as error:
        log.warning(
            "periodic round failed", extra={"round": name, "error": f"{type(error).__name__}: {error}"[:300]}
        )
        return 0


def needs_report(last: dict[str, Any] | None, clusters: int) -> bool:
    """A campaign gets a report when it is new, when it grew by half since the last one, or when the
    last attempt failed more than 20 minutes ago."""
    if last is None:
        return True
    if last["status"] == "failed":
        return bool(last["stale"])
    return clusters >= max(float(last["clusters"]) * 1.5, float(last["clusters"]) + 2)


def campaign_round(connection: Connection, chat: ChatClient) -> int:
    """Find campaigns (clusters sharing accounts) and write a report for the first that needs one."""
    scores = {r["id"]: float(r["score"]) for r in connection.execute(CAMPAIGN_CANDIDATES).fetchall()}
    if len(scores) < 2:
        return 0
    links = [(r["a"], r["b"], int(r["n"])) for r in connection.execute(CAMPAIGN_LINKS, {"ids": list(scores)})]
    for group in campaigns.find_campaigns(scores, links)[:10]:
        key = campaigns.campaign_key(group)
        if not needs_report(connection.execute(LAST_CAMPAIGN, (key,)).fetchone(), len(group)):
            continue
        ids = group[: campaigns.MAX_CLUSTERS]
        clusters = sorted(
            connection.execute("SELECT * FROM clusters WHERE id = ANY(%s)", (ids,)).fetchall(),
            key=lambda c: ids.index(c["id"]),
        )
        members = connection.execute(CAMPAIGN_MEMBERS, (ids,)).fetchall()
        posts = connection.execute(
            "SELECT cluster_id, account_hash, posted_at, text FROM cluster_posts WHERE cluster_id = ANY(%s)",
            (ids,),
        ).fetchall()
        findings = {
            r["cluster_id"]: r["report"] for r in connection.execute(CAMPAIGN_FINDINGS, (ids,)) if r["report"]
        }
        pack = campaigns.build_pack(clusters, members, posts, findings)
        log.info(
            "campaign report", extra={"campaign": key, "clusters": len(ids), "accounts": len(pack.accounts)}
        )
        result = campaigns.investigate_campaign(chat, key, pack)
        report = result.report
        connection.execute(
            SAVE_CAMPAIGN,
            {
                "key": key,
                "cluster_ids": ids,
                "accounts": len({m["account_hash"] for m in members}),
                "status": "done" if report else "failed",
                "title": report.title if report else None,
                "kind": report.kind if report else None,
                "verdict": report.verdict if report else None,
                "report": Jsonb(report.model_dump()) if report else None,
                "evidence": Jsonb(campaigns.evidence_summary(pack, result)),
                "model": chat.model,
                "prompt_version": campaigns.PROMPT_VERSION,
                "calls": result.calls,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "latency_ms": result.latency_ms,
                "error": result.error,
            },
        )
        log.info(
            "campaign report stored",
            extra={"campaign": key, "verdict": report.verdict if report else None, "error": result.error},
        )
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="FactsLens investigator (Nemotron)")
    parser.add_argument("--once", metavar="CLUSTER_ID", help="investigate one cluster and exit")
    parser.add_argument("--campaign-once", action="store_true", help="write one campaign report and exit")
    parser.add_argument(
        "--stories-once", action="store_true", help="run one contested-stories round and exit"
    )
    parser.add_argument(
        "--recycled-once", action="store_true", help="run one recycled-picture round and exit"
    )
    args = parser.parse_args()
    settings = get_settings()

    with psycopg.connect(db.database_url(settings), autocommit=True, row_factory=dict_row) as connection:

        def log_call(record: CallRecord) -> None:
            connection.execute(LOG_CALL, record.__dict__)

        chat = ChatClient.from_settings(settings, logger=log_call)
        agent = Investigator(chat, EvidenceGatherer(), KnownLists.load(settings.ground_truth_dir))
        if args.once:
            investigate_one(connection, agent, args.once)
            return
        if args.campaign_once:
            campaign_round(connection, chat)
            return
        if args.stories_once:
            story_round(connection, chat, agent.gatherer, agent.lists)
            return
        if args.recycled_once:
            print("judgements stored:", recycled_round(connection, chat, agent.gatherer))
            return

        counters: dict[str, Any] = {
            "investigations": 0,
            "failed": 0,
            "budget_waits": 0,
            "last_verdict": None,
            "campaign_reports": 0,
        }
        next_campaign_at = time.monotonic() + CAMPAIGN_FIRST_AFTER_SECONDS
        next_story_at = time.monotonic() + STORY_FIRST_AFTER_SECONDS
        next_recycled_at = time.monotonic() + 30.0
        counters["recycled_judgements"] = 0
        counters["stories"] = 0
        log.info("investigator started", extra={"model": chat.model, "prompt_version": PROMPT_VERSION})
        try:
            while True:
                pause = IDLE_SECONDS
                try:
                    row = connection.execute(NEXT_CLUSTER).fetchone()
                    if row is not None:
                        result = investigate_one(connection, agent, row["id"])
                        if result is not None:
                            counters["investigations"] += 1
                            counters["failed"] += result.report is None
                            counters["last_verdict"] = result.report.verdict if result.report else None
                        pause = BETWEEN_INVESTIGATIONS_SECONDS
                    # Periodic rounds: an error in one (e.g. an NVIDIA rate limit) is logged and the
                    # round retried next time; it must never stop the investigator.
                    if time.monotonic() >= next_campaign_at:
                        next_campaign_at = time.monotonic() + CAMPAIGN_EVERY_SECONDS
                        counters["campaign_reports"] += periodic(
                            "campaigns", campaign_round, connection, chat
                        )
                    if time.monotonic() >= next_story_at:
                        next_story_at = time.monotonic() + STORY_EVERY_SECONDS
                        counters["stories"] += periodic(
                            "stories", story_round, connection, chat, agent.gatherer, agent.lists
                        )
                    if time.monotonic() >= next_recycled_at:
                        next_recycled_at = time.monotonic() + RECYCLED_EVERY_SECONDS
                        counters["recycled_judgements"] += periodic(
                            "recycled pictures", recycled_round, connection, chat, agent.gatherer
                        )
                except BudgetExhausted as exhausted:
                    counters["budget_waits"] += 1
                    log.warning("call budget used up; waiting", extra={"detail": str(exhausted)})
                    pause = BUDGET_WAIT_SECONDS
                except psycopg.Error as error:
                    log.warning("database error", extra={"error": str(error)[:300]})
                connection.execute(
                    """
                    INSERT INTO service_status (service, updated_at, data) VALUES (%s, now(), %s)
                    ON CONFLICT (service) DO UPDATE SET updated_at = now(), data = EXCLUDED.data
                    """,
                    (
                        SERVICE,
                        Jsonb(
                            counters
                            | {
                                "calls_last_hour": chat.budget.used_last_hour(),
                                "budget_per_hour": chat.budget.per_hour,
                                "model": chat.model,
                                "prompt_version": PROMPT_VERSION,
                            }
                        ),
                    ),
                )
                time.sleep(pause)
        except KeyboardInterrupt:
            log.info("stopped by user")


if __name__ == "__main__":
    configure_logging()
    main()
