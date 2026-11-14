"""Stage 2 service: fetch profiles for accounts in candidate clusters, then score the clusters.

python -m factslens.enrich
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg.types.json import Jsonb

from factslens.common import db
from factslens.common.logging import configure_logging
from factslens.common.settings import get_settings
from factslens.detector.scoring import Evidence, Profile, assess
from factslens.enrich.lists import KnownLists
from factslens.enrich.profiles import ProfileClient

log = logging.getLogger("factslens.enrich")

SERVICE = "enrich"
PROFILE_BATCH = 100  # accounts per round (4 AppView calls)
SCORE_BATCH = 50  # clusters per round
REFETCH_AFTER = "12 hours"
LISTS_RELOAD_SECONDS = 3600.0
IDLE_SECONDS = 2.0

ACCOUNTS_TO_FETCH = f"""
SELECT did FROM accounts
WHERE did IS NOT NULL AND (fetched_at IS NULL OR fetched_at < now() - interval '{REFETCH_AFTER}')
ORDER BY fetched_at NULLS FIRST, last_seen DESC
LIMIT %s
"""

SAVE_PROFILE = """
UPDATE accounts SET handle = %(handle)s, display_name = %(display_name)s, description = %(description)s,
    created_at = %(created_at)s, followers = %(followers)s, follows = %(follows)s, posts = %(posts)s,
    has_avatar = %(has_avatar)s, has_banner = %(has_banner)s, labels = %(labels)s,
    status = 'active', fetched_at = now()
WHERE did = %(did)s
"""

MARK_UNAVAILABLE = "UPDATE accounts SET status = 'unavailable', fetched_at = now() WHERE did = %s"

CLUSTERS_TO_SCORE = """
SELECT id, kind, label, stats, domains FROM clusters
WHERE scored_at IS NULL OR scored_at < updated_at
   OR (tier = 'pending' AND scored_at < now() - interval '1 minute')
ORDER BY updated_at DESC
LIMIT %s
"""

CLUSTER_MEMBERS = """
SELECT m.did, a.fetched_at, a.status, a.handle, a.created_at, a.followers, a.follows, a.has_avatar,
       a.description,
       (SELECT count(DISTINCT o.cluster_id) FROM cluster_members o JOIN clusters c ON c.id = o.cluster_id
         WHERE o.account_hash = m.account_hash AND o.cluster_id <> m.cluster_id
           AND c.last_seen > now() - interval '24 hours') AS other_clusters
FROM cluster_members m LEFT JOIN accounts a ON a.account_hash = m.account_hash
WHERE m.cluster_id = %s
"""

SAVE_SCORE = """
UPDATE clusters SET signals = %s, score = %s, tier = %s, scored_at = now() WHERE id = %s
"""


def fetch_profiles(connection: psycopg.Connection[tuple[Any, ...]], client: ProfileClient) -> int:
    dids = [row[0] for row in connection.execute(ACCOUNTS_TO_FETCH, (PROFILE_BATCH,)).fetchall()]
    if not dids:
        return 0
    profiles = client.fetch(dids)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.executemany(
            SAVE_PROFILE,
            [
                {
                    "did": p.did,
                    "handle": p.handle,
                    "display_name": p.display_name,
                    "description": p.description,
                    "created_at": p.created_at,
                    "followers": p.followers,
                    "follows": p.follows,
                    "posts": p.posts,
                    "has_avatar": p.has_avatar,
                    "has_banner": p.has_banner,
                    "labels": list(p.labels),
                }
                for p in profiles.values()
            ],
        )
        cursor.executemany(MARK_UNAVAILABLE, [(d,) for d in dids if d not in profiles])
    return len(dids)


def evidence_for(
    connection: psycopg.Connection[tuple[Any, ...]], cluster: tuple[Any, ...], lists: KnownLists
) -> Evidence:
    cluster_id, kind, label, stats, domains = cluster
    profiles: list[Profile | None] = []
    others: list[int] = []
    known = listed = 0
    for (
        did,
        fetched_at,
        status,
        handle,
        created,
        followers,
        follows,
        avatar,
        bio,
        other,
    ) in connection.execute(CLUSTER_MEMBERS, (cluster_id,)).fetchall():
        others.append(int(other))
        if fetched_at is None:
            profiles.append(None)
        else:
            profiles.append(
                Profile(
                    available=status == "active",
                    handle=handle,
                    created_at=created,
                    followers=followers,
                    follows=follows,
                    has_avatar=avatar,
                    description=bio,
                )
            )
        known += lists.is_known_account(did, handle)
        listed += lists.on_listed_server(handle)
    linked = [d for d, _ in domains]
    urls = [label] if kind == "link" else []
    if kind == "link" and (host := urlsplit(label).hostname):
        linked.append(host)
    if kind == "domain":
        linked.append(label)
    return Evidence(
        kind=kind,
        stats=stats,
        profiles=profiles,
        other_clusters=others,
        now=datetime.now(UTC),
        known_accounts=known,
        listed_server_members=listed,
        known_domains=sorted(set(lists.domain_hits(linked))),
        pravda_channels=sorted(set(lists.channel_hits(urls))),
        lists_loaded=lists.loaded,
    )


def score_clusters(connection: psycopg.Connection[tuple[Any, ...]], lists: KnownLists) -> int:
    clusters = connection.execute(CLUSTERS_TO_SCORE, (SCORE_BATCH,)).fetchall()
    for cluster in clusters:
        result = assess(evidence_for(connection, cluster, lists))
        payload = {
            "signals": [s.as_dict() for s in result.signals],
            "reasons": result.reasons,
            "profile_coverage": result.profile_coverage,
        }
        connection.execute(SAVE_SCORE, (Jsonb(payload), result.score, result.tier.value, cluster[0]))
    return len(clusters)


def main() -> None:
    settings = get_settings()
    client = ProfileClient()
    counters = {"profiles_fetched": 0, "clusters_scored": 0, "errors": 0}
    with psycopg.connect(db.database_url(settings), autocommit=True, connect_timeout=15) as connection:
        lists = KnownLists.load(settings.ground_truth_dir)
        lists_loaded_at = time.monotonic()
        log.info("enrich started", extra={"lists_loaded": lists.loaded})
        try:
            while True:
                started = time.monotonic()
                try:
                    fetched = fetch_profiles(connection, client)
                    scored = score_clusters(connection, lists)
                    counters["profiles_fetched"] += fetched
                    counters["clusters_scored"] += scored
                except (psycopg.Error, RuntimeError, OSError) as error:
                    counters["errors"] += 1
                    fetched = scored = 0
                    log.warning("enrich round failed", extra={"error": str(error)[:300]})
                if time.monotonic() - lists_loaded_at >= LISTS_RELOAD_SECONDS:
                    lists, lists_loaded_at = KnownLists.load(settings.ground_truth_dir), time.monotonic()
                data = counters | {
                    "appview_calls": client.calls,
                    "appview_rate_limited": client.rate_limited,
                    "lists_loaded": lists.loaded,
                    "round_seconds": round(time.monotonic() - started, 2),
                }
                connection.execute(
                    """
                    INSERT INTO service_status (service, updated_at, data) VALUES (%s, now(), %s)
                    ON CONFLICT (service) DO UPDATE SET updated_at = now(), data = EXCLUDED.data
                    """,
                    (SERVICE, Jsonb(data)),
                )
                if not fetched and not scored:
                    time.sleep(IDLE_SECONDS)
        except KeyboardInterrupt:
            log.info("stopped by user")


if __name__ == "__main__":
    configure_logging()
    main()
