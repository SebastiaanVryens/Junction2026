"""Writing and reading clusters in PostgreSQL (schema: migrations/002_clusters.sql)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from factslens.common.privacy import RAW_RETENTION_HOURS, AccountHasher
from factslens.detector.groups import GroupSnapshot

type SyncConnection = psycopg.Connection[tuple[Any, ...]]


def ts(time_us: int) -> datetime:
    return datetime.fromtimestamp(time_us / 1_000_000, UTC)


UPSERT_CLUSTER = """
INSERT INTO clusters (id, kind, label, first_seen, last_seen, accounts, events, stats, per_minute,
                      domains, languages, updated_at)
VALUES (%(id)s, %(kind)s, %(label)s, %(first_seen)s, %(last_seen)s, %(accounts)s, %(events)s,
        %(stats)s, %(per_minute)s, %(domains)s, %(languages)s, now())
ON CONFLICT (id) DO UPDATE SET
    label = EXCLUDED.label, first_seen = LEAST(clusters.first_seen, EXCLUDED.first_seen),
    last_seen = EXCLUDED.last_seen, accounts = EXCLUDED.accounts, events = EXCLUDED.events,
    stats = EXCLUDED.stats, per_minute = EXCLUDED.per_minute, domains = EXCLUDED.domains,
    languages = EXCLUDED.languages, updated_at = now()
"""

UPSERT_MEMBER = """
INSERT INTO cluster_members (cluster_id, account_hash, did, events, first_seen, last_seen)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (cluster_id, account_hash) DO UPDATE SET
    did = EXCLUDED.did, events = EXCLUDED.events, last_seen = EXCLUDED.last_seen
"""

UPSERT_ACCOUNT_SEEN = """
INSERT INTO accounts (account_hash, did, last_seen) VALUES (%s, %s, %s)
ON CONFLICT (account_hash) DO UPDATE SET did = EXCLUDED.did,
    last_seen = GREATEST(accounts.last_seen, EXCLUDED.last_seen)
"""

INSERT_POST = """
INSERT INTO cluster_posts
    (cluster_id, post_id, account_hash, posted_at, text, bridged, media, media_refs, links)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (cluster_id, post_id) DO NOTHING
"""


def save_snapshot(connection: SyncConnection, snapshot: GroupSnapshot, hasher: AccountHasher) -> None:
    """Upsert one candidate with its members and sample posts, in one transaction."""
    hashes = {author: hasher(author) for author in snapshot.members}
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            UPSERT_CLUSTER,
            {
                "id": snapshot.key,
                "kind": snapshot.kind.value,
                "label": snapshot.label,
                "first_seen": ts(snapshot.first_us),
                "last_seen": ts(snapshot.last_us),
                "accounts": snapshot.accounts,
                "events": snapshot.events,
                "stats": Jsonb(snapshot.stats),
                "per_minute": list(snapshot.per_minute),
                "domains": Jsonb(snapshot.domains),
                "languages": Jsonb(snapshot.languages),
            },
        )
        members = sorted(snapshot.members.items())  # fixed order avoids deadlocks between writers
        cursor.executemany(
            UPSERT_MEMBER,
            [(snapshot.key, hashes[a], a, n, ts(first), ts(last)) for a, (n, first, last) in members],
        )
        cursor.executemany(UPSERT_ACCOUNT_SEEN, [(hashes[a], a, ts(last)) for a, (_, _, last) in members])
        cursor.executemany(
            INSERT_POST,
            [
                (
                    snapshot.key,
                    i.post_id,
                    hashes[i.author],
                    ts(i.time_us),
                    i.text,
                    i.bridged,
                    len(i.media),
                    Jsonb([{"kind": kind, "ref": ref} for kind, ref in i.media]),
                    list(i.links),
                )
                for i in snapshot.sample
            ],
        )


RETENTION_SQL: Sequence[str] = (
    # Raw ids, names and post texts leave after 72 h; hashes and aggregate statistics stay.
    "UPDATE cluster_members SET did = NULL"
    " WHERE did IS NOT NULL AND last_seen < now() - %(window)s::interval",
    # A handle, display name or bio identifies a person as well as the id does.
    "UPDATE accounts SET did = NULL, handle = NULL, display_name = NULL, description = NULL"
    " WHERE (did IS NOT NULL OR handle IS NOT NULL OR display_name IS NOT NULL OR description IS NOT NULL)"
    " AND last_seen < now() - %(window)s::interval",
    "DELETE FROM cluster_posts WHERE posted_at < now() - %(window)s::interval",
    # Investigations keep the report, but not the post ids (which contain account ids) behind P1, P2...
    "UPDATE investigations SET evidence = evidence - 'posts'"
    " WHERE evidence ? 'posts' AND created_at < now() - %(window)s::interval",
    # Campaign reports can quote posts.
    "DELETE FROM campaigns WHERE created_at < now() - %(window)s::interval",
    "DELETE FROM stories WHERE created_at < now() - %(window)s::interval",
    "DELETE FROM image_hashes WHERE posted_at < now() - %(window)s::interval",
    "DELETE FROM image_reuse WHERE later_at < now() - %(window)s::interval",
)


def apply_retention(connection: SyncConnection) -> dict[str, int]:
    window = f"{RAW_RETENTION_HOURS} hours"
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for statement in RETENTION_SQL:
            cursor.execute(statement, {"window": window})
            counts[" ".join(statement.split()[:3])] = cursor.rowcount
    return counts
