"""Read-only queries the web app needs, behind one interface so tests can use a fake."""

from __future__ import annotations

from typing import Any, Protocol

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from factslens.common import db, topics

TIERS = ("suspicious", "watch", "pending", "declared_automation", "likely_organic")


class Repository(Protocol):
    async def statuses(self) -> dict[str, db.ServiceStatus]: ...

    async def tier_counts(self, hours: int) -> dict[str, int]: ...

    async def clusters(
        self, tier: str | None, hours: int, limit: int, unreviewed: bool = False
    ) -> list[dict[str, Any]]: ...

    async def reviewed_count(self, hours: int) -> int: ...

    async def cluster(self, cluster_id: str) -> dict[str, Any] | None: ...

    async def members(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def posts(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def related(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def investigation(self, cluster_id: str) -> dict[str, Any] | None: ...

    async def latest_eval_run(self) -> dict[str, Any] | None: ...

    async def review_counts(
        self, sampled_only: bool = False, topic_only: bool = False
    ) -> list[tuple[str | None, str, str | None, int]]: ...

    async def nemotron_stats(self) -> dict[str, Any]: ...

    async def reviews(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def add_review(
        self,
        cluster_id: str,
        decision: str,
        note: str | None,
        tier: str | None,
        verdict: str | None,
        category: str | None = None,
        sampled: bool = False,
    ) -> None: ...

    async def next_unreviewed(self, after: str | None, hours: int, sample: bool = False) -> str | None: ...

    async def log_model_call(self, record: dict[str, Any]) -> None: ...

    async def live_findings(self) -> dict[str, Any]: ...

    async def campaigns(self, hours: int) -> list[dict[str, Any]]: ...

    async def lens_matches(
        self, post_id: str | None, author: str | None, links: list[str], domains: list[str], hours: int
    ) -> list[dict[str, Any]]: ...

    async def text_clusters(self, hours: int) -> list[dict[str, Any]]: ...

    async def hold_nemotron(self, seconds: float) -> None: ...

    async def latest_stories(self, limit: int) -> list[dict[str, Any]]: ...

    async def network_swarms(self, hours: int, on_topic: bool, limit: int) -> list[dict[str, Any]]: ...

    async def ask_overview(self, hours: int) -> dict[str, Any]: ...

    async def ask_groups(
        self, patterns: list[str], tier: str | None, platform: str | None, hours: int
    ) -> list[dict[str, Any]]: ...

    async def ask_group(self, cluster_id: str) -> dict[str, Any] | None: ...

    async def ask_domains(self, hours: int) -> list[dict[str, Any]]: ...

    async def ask_members(self, hours: int) -> list[dict[str, Any]]: ...

    async def graph_clusters(self, ids: list[str] | None, hours: int, limit: int) -> list[dict[str, Any]]: ...

    async def graph_members(self, cluster_ids: list[str]) -> list[dict[str, Any]]: ...

    async def graph_shared(self, cluster_ids: list[str]) -> list[dict[str, Any]]: ...

    async def graph_reuse(self, cluster_ids: list[str]) -> list[dict[str, Any]]: ...

    async def graph_stories(self, hours: int, cluster_id: str | None = None) -> list[dict[str, Any]]: ...

    async def recycled_for_cluster(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def stories(self, hours: int) -> list[dict[str, Any]]: ...

    async def story(self, story_id: int) -> dict[str, Any] | None: ...

    async def stories_for_cluster(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def campaign(self, campaign_id: int) -> dict[str, Any] | None: ...

    async def campaign_for_cluster(self, cluster_id: str) -> dict[str, Any] | None: ...

    async def accounts_by_hash(self, hashes: list[str]) -> dict[str, dict[str, Any]]: ...

    async def add_account_review(self, cluster_id: str, account_hash: str, decision: str) -> None: ...

    async def account_marks(self, account_hashes: list[str]) -> dict[str, dict[str, Any]]: ...

    async def account_review_counts(self) -> dict[str, int]: ...

    async def review_categories(self) -> dict[str, int]: ...


class PostgresRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def _all(self, sql: str, params: tuple[Any, ...] | dict[str, Any]) -> list[dict[str, Any]]:
        async with self._pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(sql, params)
            return list(await cursor.fetchall())

    async def statuses(self) -> dict[str, db.ServiceStatus]:
        async with self._pool.connection() as connection:
            return await db.read_statuses(connection)

    async def tier_counts(self, hours: int) -> dict[str, int]:
        rows = await self._all(
            "SELECT coalesce(tier, 'pending') AS tier, count(*) AS n FROM clusters "
            "WHERE last_seen > now() - make_interval(hours => %s) GROUP BY 1",
            (hours,),
        )
        return {row["tier"]: int(row["n"]) for row in rows}

    async def clusters(
        self, tier: str | None, hours: int, limit: int, unreviewed: bool = False
    ) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT id, kind, label, first_seen, last_seen, accounts, events, score,
                   coalesce(tier, 'pending') AS tier, signals,
                   (SELECT i.verdict FROM investigations i
                    WHERE i.cluster_id = clusters.id AND i.status = 'done'
                    ORDER BY i.created_at DESC LIMIT 1) AS verdict,
                   rv.confirmed, rv.false_alarm, rv.unsure, rv.reviews
            FROM clusters
            LEFT JOIN LATERAL (
                SELECT count(*) FILTER (WHERE r.decision = 'confirm') AS confirmed,
                       count(*) FILTER (WHERE r.decision = 'false_alarm') AS false_alarm,
                       count(*) FILTER (WHERE r.decision = 'unsure') AS unsure,
                       count(*) AS reviews
                FROM reviews r WHERE r.cluster_id = clusters.id
            ) rv ON true
            WHERE last_seen > now() - make_interval(hours => %s)
              AND (%s::text IS NULL OR coalesce(tier, 'pending') = %s)
              AND (NOT %s OR rv.reviews = 0)
            ORDER BY score DESC NULLS LAST, last_seen DESC
            LIMIT %s
            """,
            (hours, tier, tier, unreviewed, limit),
        )

    async def reviewed_count(self, hours: int) -> int:
        rows = await self._all(
            "SELECT count(*) AS n FROM clusters c WHERE c.last_seen > now() - make_interval(hours => %s) "
            "AND EXISTS (SELECT 1 FROM reviews r WHERE r.cluster_id = c.id)",
            (hours,),
        )
        return int(rows[0]["n"]) if rows else 0

    async def cluster(self, cluster_id: str) -> dict[str, Any] | None:
        rows = await self._all(
            "SELECT *, coalesce(tier, 'pending') AS tier_or_pending FROM clusters WHERE id = %s",
            (cluster_id,),
        )
        return rows[0] if rows else None

    async def members(self, cluster_id: str) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT m.account_hash, m.events, m.first_seen, m.last_seen, a.handle, a.display_name,
                   a.created_at, a.followers, a.follows, a.posts, a.has_avatar, a.status, a.labels,
                   coalesce(a.on_public_list, false) AS on_public_list, m.did
            FROM cluster_members m LEFT JOIN accounts a ON a.account_hash = m.account_hash
            WHERE m.cluster_id = %s
            ORDER BY m.first_seen
            LIMIT 500
            """,
            (cluster_id,),
        )

    async def posts(self, cluster_id: str) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT p.post_id, p.posted_at, p.text, p.bridged, p.media, p.media_refs, a.handle, a.status,
                   coalesce(a.on_public_list, false) AS on_public_list
            FROM cluster_posts p LEFT JOIN accounts a ON a.account_hash = p.account_hash
            WHERE p.cluster_id = %s
            ORDER BY p.posted_at
            LIMIT 50
            """,
            (cluster_id,),
        )

    async def related(self, cluster_id: str) -> list[dict[str, Any]]:
        """Other recent clusters that share accounts with this one."""
        return await self._all(
            """
            SELECT c.id, c.kind, c.label, coalesce(c.tier, 'pending') AS tier, c.score,
                   count(*) AS shared_accounts
            FROM cluster_members m
            JOIN cluster_members o ON o.account_hash = m.account_hash AND o.cluster_id <> m.cluster_id
            JOIN clusters c ON c.id = o.cluster_id AND c.last_seen > now() - interval '24 hours'
            WHERE m.cluster_id = %s
            GROUP BY c.id
            HAVING count(*) >= 2
            ORDER BY count(*) DESC
            LIMIT 12
            """,
            (cluster_id,),
        )

    async def investigation(self, cluster_id: str) -> dict[str, Any] | None:
        rows = await self._all(
            """
            SELECT created_at, status, verdict, report, evidence, model, prompt_version, calls,
                   prompt_tokens, completion_tokens, latency_ms, error, accounts_at
            FROM investigations WHERE cluster_id = %s ORDER BY created_at DESC LIMIT 1
            """,
            (cluster_id,),
        )
        return rows[0] if rows else None

    async def reviews(self, cluster_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT created_at, decision, note, tier_at, verdict_at, category FROM reviews "
            "WHERE cluster_id = %s ORDER BY created_at DESC LIMIT 20",
            (cluster_id,),
        )

    async def add_review(
        self,
        cluster_id: str,
        decision: str,
        note: str | None,
        tier: str | None,
        verdict: str | None,
        category: str | None = None,
        sampled: bool = False,
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "INSERT INTO reviews (cluster_id, decision, note, tier_at, verdict_at, category, sampled) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (cluster_id, decision, note, tier, verdict, category, sampled),
            )

    async def next_unreviewed(self, after: str | None, hours: int, sample: bool = False) -> str | None:
        """The next suspicious or watch cluster nobody has reviewed, in list order (highest score
        first) after `after`; from the top again when the end is reached. With `sample`, a random
        unreviewed cluster from every scored tier instead, each equally likely."""
        if sample:
            rows = await self._all(
                """
                SELECT c.id FROM clusters c
                WHERE c.last_seen > now() - make_interval(hours => %(hours)s)
                  AND c.tier IN ('suspicious', 'watch', 'likely_organic', 'declared_automation')
                  AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.cluster_id = c.id)
                  AND c.id <> coalesce(%(after)s, '')
                ORDER BY random() LIMIT 1
                """,
                {"hours": hours, "after": after},
            )
            return str(rows[0]["id"]) if rows else None
        # Groups on our topics come first; within each, highest score first.
        query = """
            SELECT c.id FROM clusters c
            WHERE c.last_seen > now() - make_interval(hours => %(hours)s)
              AND coalesce(c.tier, 'pending') IN ('suspicious', 'watch')
              AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.cluster_id = c.id)
              AND c.id <> coalesce(%(after)s, '')
              {topic_clause}
              {after_clause}
            ORDER BY coalesce(c.score, -1) DESC, c.last_seen DESC, c.id DESC
            LIMIT 1
            """
        after_clause = """AND (coalesce(c.score, -1), c.last_seen, c.id) < (
                  SELECT coalesce(score, -1), last_seen, id FROM clusters WHERE id = %(after)s)"""
        topic_clause = """AND concat_ws(' ', c.label, (
                    SELECT concat_ws(' ', i.report->>'summary', i.report->>'narrative')
                    FROM investigations i WHERE i.cluster_id = c.id AND i.status = 'done'
                    ORDER BY i.created_at DESC LIMIT 1)) ~* %(topic)s"""
        params = {"hours": hours, "after": after, "topic": topics.SQL_PATTERN}
        for topic in (topic_clause, ""):
            if after is not None:
                rows = await self._all(query.format(topic_clause=topic, after_clause=after_clause), params)
                if rows:
                    return str(rows[0]["id"])
            rows = await self._all(query.format(topic_clause=topic, after_clause=""), params)
            if rows:
                return str(rows[0]["id"])
        return None

    async def log_model_call(self, record: dict[str, Any]) -> None:
        """Every Nemotron call is logged, also the reviewer-triggered translations."""
        async with self._pool.connection() as connection:
            await connection.execute(
                "INSERT INTO model_calls (purpose, cluster_id, provider, model, status, attempts, "
                "latency_ms, "
                "prompt_tokens, completion_tokens, reasoning_tokens, error) VALUES (%(purpose)s, "
                "%(cluster_id)s, %(provider)s, %(model)s, %(status)s, %(attempts)s, %(latency_ms)s, "
                "%(prompt_tokens)s, %(completion_tokens)s, %(reasoning_tokens)s, %(error)s)",
                record,
            )

    async def add_account_review(self, cluster_id: str, account_hash: str, decision: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "INSERT INTO account_reviews (cluster_id, account_hash, decision) VALUES (%s, %s, %s)",
                (cluster_id, account_hash, decision),
            )

    async def account_marks(self, account_hashes: list[str]) -> dict[str, dict[str, Any]]:
        """Latest per-account mark (from any cluster) for these accounts; 'clear' removes a mark."""
        if not account_hashes:
            return {}
        rows = await self._all(
            """
            SELECT DISTINCT ON (account_hash) account_hash, decision, cluster_id, created_at
            FROM account_reviews WHERE account_hash = ANY(%s)
            ORDER BY account_hash, created_at DESC
            """,
            (account_hashes,),
        )
        return {r["account_hash"]: r for r in rows if r["decision"] != "clear"}

    async def account_review_counts(self) -> dict[str, int]:
        rows = await self._all(
            """
            WITH latest AS (
                SELECT DISTINCT ON (account_hash) account_hash, decision
                FROM account_reviews ORDER BY account_hash, created_at DESC
            )
            SELECT decision, count(*) AS n FROM latest WHERE decision <> 'clear' GROUP BY 1
            """,
            (),
        )
        return {r["decision"]: int(r["n"]) for r in rows}

    async def campaigns(self, hours: int) -> list[dict[str, Any]]:
        """Latest successful report per campaign, biggest first."""
        return await self._all(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (campaign_key) id, campaign_key, created_at, cluster_ids, accounts, title,
                       kind, verdict, report
                FROM campaigns
                WHERE status = 'done' AND created_at > now() - make_interval(hours => %s)
                ORDER BY campaign_key, created_at DESC
            ) latest
            ORDER BY cardinality(cluster_ids) DESC, accounts DESC
            """,
            (hours,),
        )

    async def recycled_for_cluster(self, cluster_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT media_ref, hash_distance, earlier_cluster, earlier_at, earlier_caption, earlier_source, "
            "later_cluster, later_at, later_caption, later_source, supports, judgement FROM image_reuse "
            "WHERE status = 'done' AND (later_cluster = %s OR earlier_cluster = %s) "
            "ORDER BY later_at DESC LIMIT 10",
            (cluster_id, cluster_id),
        )

    async def graph_clusters(self, ids: list[str] | None, hours: int, limit: int) -> list[dict[str, Any]]:
        """Clusters for the graph: the given ids, or the flagged ones of the window, highest score first."""
        return await self._all(
            """
            SELECT id, kind, label, coalesce(tier, 'pending') AS tier, score, accounts, domains FROM clusters
            WHERE (%(ids)s::text[] IS NOT NULL AND id = ANY(%(ids)s))
               OR (%(ids)s::text[] IS NULL AND tier IN ('suspicious', 'watch')
                   AND last_seen > now() - make_interval(hours => %(hours)s))
            ORDER BY score DESC NULLS LAST LIMIT %(limit)s
            """,
            {"ids": ids, "hours": hours, "limit": limit},
        )

    async def graph_members(self, cluster_ids: list[str]) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT m.cluster_id, m.account_hash, a.handle, a.status, coalesce(a.on_public_list, false) "
            "AS on_public_list FROM cluster_members m "
            "LEFT JOIN accounts a ON a.account_hash = m.account_hash "
            "WHERE m.cluster_id = ANY(%s)",
            (cluster_ids,),
        )

    async def graph_shared(self, cluster_ids: list[str]) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT m.cluster_id AS a, o.cluster_id AS b, count(*) AS n FROM cluster_members m "
            "JOIN cluster_members o ON o.account_hash = m.account_hash AND o.cluster_id < m.cluster_id "
            "WHERE m.cluster_id = ANY(%(ids)s) AND o.cluster_id = ANY(%(ids)s) "
            "GROUP BY 1, 2 HAVING count(*) >= 2",
            {"ids": cluster_ids},
        )

    async def graph_reuse(self, cluster_ids: list[str]) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT earlier_cluster, later_cluster, supports FROM image_reuse WHERE status = 'done' "
            "AND (earlier_cluster = ANY(%(ids)s) OR later_cluster = ANY(%(ids)s))",
            {"ids": cluster_ids},
        )

    async def graph_stories(self, hours: int, cluster_id: str | None = None) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT id, title, report, evidence, cluster_ids FROM stories WHERE status = 'done' "
            "AND created_at > now() - make_interval(hours => %(hours)s) "
            "AND (%(cluster)s::text IS NULL OR %(cluster)s = ANY(cluster_ids)) "
            "ORDER BY created_at DESC LIMIT 30",
            {"hours": hours, "cluster": cluster_id},
        )

    async def lens_matches(
        self, post_id: str | None, author: str | None, links: list[str], domains: list[str], hours: int
    ) -> list[dict[str, Any]]:
        """Clusters of the window that contain this post, this author, one of its links or its
        website, with how they matched, the latest verdict, campaign and stories."""
        return await self._all(
            """
            WITH hits AS (
                SELECT cluster_id, 'the same post' AS how FROM cluster_posts WHERE post_id = %(post)s
                UNION SELECT m.cluster_id, 'the same author' FROM accounts a
                    JOIN cluster_members m ON m.account_hash = a.account_hash WHERE a.did = %(author)s
                UNION SELECT cluster_id, 'the same link' FROM cluster_posts WHERE links && %(links)s::text[]
                UNION SELECT id, 'the same link' FROM clusters
                    WHERE kind = 'link' AND label = ANY(%(links)s::text[])
                UNION SELECT id, 'the same website' FROM clusters
                    WHERE last_seen > now() - make_interval(hours => %(hours)s)
                      AND ((kind = 'domain' AND label = ANY(%(domains)s::text[]))
                       OR EXISTS (SELECT 1 FROM jsonb_array_elements(domains) d
                                  WHERE d->>0 = ANY(%(domains)s::text[])))
            )
            SELECT c.id, c.kind, c.label, coalesce(c.tier, 'pending') AS tier, c.score, c.accounts,
                   array_agg(DISTINCT h.how) AS how,
                   (SELECT i.verdict FROM investigations i WHERE i.cluster_id = c.id AND i.status = 'done'
                    ORDER BY i.created_at DESC LIMIT 1) AS verdict
            FROM hits h JOIN clusters c ON c.id = h.cluster_id
            WHERE c.last_seen > now() - make_interval(hours => %(hours)s)
            GROUP BY c.id ORDER BY c.score DESC NULLS LAST LIMIT 12
            """,
            {"post": post_id, "author": author, "links": links, "domains": domains, "hours": hours},
        )

    async def network_swarms(self, hours: int, on_topic: bool, limit: int) -> list[dict[str, Any]]:
        """The strongest suspicious or watched groups of the window, on our topics or not."""
        return await self._all(
            """
            SELECT * FROM (
                SELECT c.id, c.kind, c.label, coalesce(c.tier, 'pending') AS tier, c.score, c.accounts,
                       c.signals, c.first_seen, c.last_seen, i.verdict, i.report->>'summary' AS summary,
                       concat_ws(' ', c.label, i.report->>'summary', i.report->>'narrative') ~* %(topic)s
                           AS topical
                FROM clusters c
                LEFT JOIN LATERAL (
                    SELECT verdict, report FROM investigations
                    WHERE cluster_id = c.id AND status = 'done' ORDER BY created_at DESC LIMIT 1
                ) i ON true
                WHERE c.tier IN ('suspicious', 'watch')
                  AND c.last_seen > now() - make_interval(hours => %(hours)s)
            ) found
            WHERE topical = %(on_topic)s
            ORDER BY (tier = 'suspicious') DESC, (verdict = 'staged') DESC,
                     score DESC NULLS LAST, accounts DESC
            LIMIT %(limit)s
            """,
            {"hours": hours, "on_topic": on_topic, "limit": limit, "topic": topics.SQL_PATTERN},
        )

    async def latest_stories(self, limit: int) -> list[dict[str, Any]]:
        """The newest compared stories, with their evidence (for the front page's pictures)."""
        return await self._all(
            "SELECT id, created_at, title, story_status, report, evidence FROM stories "
            "WHERE status = 'done' AND report IS NOT NULL ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )

    async def hold_nemotron(self, seconds: float) -> None:
        """Ask background services to start no new model calls for `seconds` (0 releases)."""
        async with self._pool.connection() as connection:
            await connection.execute(
                "INSERT INTO nemotron_priority (id, until) VALUES (1, now() + make_interval(secs => %s)) "
                "ON CONFLICT (id) DO UPDATE SET until = EXCLUDED.until",
                (seconds,),
            )

    async def text_clusters(self, hours: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT id, kind, label, coalesce(tier, 'pending') AS tier, score, accounts, "
            "(SELECT i.verdict FROM investigations i WHERE i.cluster_id = c.id AND i.status = 'done' "
            "ORDER BY i.created_at DESC LIMIT 1) AS verdict FROM clusters c "
            "WHERE kind = 'text' AND last_seen > now() - make_interval(hours => %s) "
            "ORDER BY score DESC NULLS LAST LIMIT 3000",
            (hours,),
        )

    # --- Ask the network: fixed, bounded, read-only queries (the model never writes SQL) ---------

    async def ask_overview(self, hours: int) -> dict[str, Any]:
        window = {"hours": hours}
        tiers = await self._all(
            "SELECT coalesce(tier, 'pending') AS tier, count(*) AS groups, sum(accounts) AS accounts "
            "FROM clusters WHERE last_seen > now() - make_interval(hours => %(hours)s) GROUP BY 1",
            window,
        )
        platforms = await self._all(
            """
            SELECT CASE WHEN p.post_id LIKE 'at://%%' THEN 'bluesky'
                        WHEN p.post_id LIKE 'https://t.me/%%' THEN 'telegram' ELSE 'mastodon' END AS platform,
                   count(DISTINCT p.cluster_id) AS flagged_groups
            FROM cluster_posts p JOIN clusters c ON c.id = p.cluster_id
            WHERE c.tier IN ('suspicious', 'watch')
              AND c.last_seen > now() - make_interval(hours => %(hours)s)
            GROUP BY 1
            """,
            window,
        )
        verdicts = await self._all(
            "SELECT verdict, count(DISTINCT cluster_id) AS groups FROM investigations "
            "WHERE status = 'done' AND created_at > now() - make_interval(hours => %(hours)s) GROUP BY 1",
            window,
        )
        counts = await self._all(
            "SELECT (SELECT count(DISTINCT campaign_key) FROM campaigns WHERE status = 'done' "
            "AND created_at > now() - make_interval(hours => %(hours)s)) AS campaigns, "
            "(SELECT count(*) FROM stories WHERE status = 'done' "
            "AND created_at > now() - make_interval(hours => %(hours)s)) AS stories",
            window,
        )
        return {
            "groups_by_tier": {r["tier"]: {"groups": r["groups"], "accounts": r["accounts"]} for r in tiers},
            "flagged_groups_by_platform": {r["platform"]: r["flagged_groups"] for r in platforms},
            "nemotron_verdicts": {r["verdict"]: r["groups"] for r in verdicts if r["verdict"]},
            **(counts[0] if counts else {}),
        }

    async def ask_groups(
        self, patterns: list[str], tier: str | None, platform: str | None, hours: int
    ) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT c.id, c.kind, c.label, coalesce(c.tier, 'pending') AS tier, c.score, c.accounts,
                   i.verdict, i.report->>'summary' AS summary
            FROM clusters c
            LEFT JOIN LATERAL (
                SELECT verdict, report FROM investigations
                WHERE cluster_id = c.id AND status = 'done' ORDER BY created_at DESC LIMIT 1
            ) i ON true
            WHERE c.last_seen > now() - make_interval(hours => %(hours)s)
              AND (%(tier)s::text IS NULL OR coalesce(c.tier, 'pending') = %(tier)s)
              AND (%(platform)s::text IS NULL OR EXISTS (
                    SELECT 1 FROM cluster_posts p WHERE p.cluster_id = c.id
                      AND CASE WHEN p.post_id LIKE 'at://%%' THEN 'bluesky'
                               WHEN p.post_id LIKE 'https://t.me/%%' THEN 'telegram'
                               ELSE 'mastodon' END = %(platform)s))
              AND (cardinality(%(patterns)s::text[]) = 0
                   OR concat_ws(' ', c.label, i.report->>'summary', i.report->>'narrative')
                      ILIKE ALL(%(patterns)s::text[])
                   OR EXISTS (SELECT 1 FROM cluster_posts p
                              WHERE p.cluster_id = c.id AND p.text ILIKE ALL(%(patterns)s::text[])))
            ORDER BY CASE coalesce(c.tier, 'pending') WHEN 'suspicious' THEN 0 WHEN 'watch' THEN 1
                     WHEN 'pending' THEN 2 ELSE 3 END, c.score DESC NULLS LAST, c.accounts DESC
            LIMIT 12
            """,
            {"patterns": patterns, "tier": tier, "platform": platform, "hours": hours},
        )

    async def ask_group(self, cluster_id: str) -> dict[str, Any] | None:
        rows = await self._all(
            """
            SELECT c.id, c.kind, c.label, coalesce(c.tier, 'pending') AS tier, c.score, c.accounts,
                   c.signals, c.first_seen, c.last_seen, i.verdict,
                   i.report->>'summary' AS summary, i.report->>'narrative' AS narrative,
                   (SELECT coalesce(jsonb_agg(t->>'name'), '[]') FROM jsonb_array_elements(
                        coalesce(i.report->'techniques', '[]')) t) AS techniques,
                   (SELECT coalesce(array_agg(text), '{}') FROM (
                        SELECT text FROM cluster_posts WHERE cluster_id = c.id AND text <> ''
                        ORDER BY posted_at LIMIT 3) s) AS sample_posts
            FROM clusters c
            LEFT JOIN LATERAL (
                SELECT verdict, report FROM investigations
                WHERE cluster_id = c.id AND status = 'done' ORDER BY created_at DESC LIMIT 1
            ) i ON true
            WHERE c.id = %s
            """,
            (cluster_id,),
        )
        if not rows:
            return None
        row = rows[0]
        row["campaign"] = await self.campaign_for_cluster(cluster_id)
        row["stories"] = [
            {"id": s["id"], "title": s["title"]} for s in await self.stories_for_cluster(cluster_id)
        ]
        return row

    async def ask_domains(self, hours: int) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT d->>0 AS domain, count(DISTINCT c.id) AS groups, sum(c.accounts) AS accounts
            FROM clusters c, jsonb_array_elements(c.domains) d
            WHERE c.tier IN ('suspicious', 'watch') AND c.last_seen > now() - make_interval(hours => %s)
              AND jsonb_typeof(d) = 'array'
            GROUP BY 1 ORDER BY 2 DESC, 3 DESC LIMIT 40
            """,
            (hours,),
        )

    async def ask_members(self, hours: int) -> list[dict[str, Any]]:
        """Members of flagged groups with their platform id and handle, only to count sources;
        the tool returns counts per channel or server, never accounts."""
        return await self._all(
            """
            SELECT m.cluster_id, m.account_hash, a.did, a.handle
            FROM cluster_members m JOIN clusters c ON c.id = m.cluster_id
            LEFT JOIN accounts a ON a.account_hash = m.account_hash
            WHERE c.tier IN ('suspicious', 'watch') AND c.last_seen > now() - make_interval(hours => %s)
            LIMIT 20000
            """,
            (hours,),
        )

    async def stories(self, hours: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT id, created_at, title, story_status, cluster_ids, report FROM stories "
            "WHERE status = 'done' AND created_at > now() - make_interval(hours => %s) "
            "ORDER BY created_at DESC",
            (hours,),
        )

    async def story(self, story_id: int) -> dict[str, Any] | None:
        rows = await self._all("SELECT * FROM stories WHERE id = %s AND status = 'done'", (story_id,))
        return rows[0] if rows else None

    async def stories_for_cluster(self, cluster_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT id, title, story_status, report->'amplification' AS amplification, "
            "evidence->'clusters' AS refs "
            "FROM stories WHERE status = 'done' AND %s = ANY(cluster_ids) "
            "AND created_at > now() - interval '24 hours' ORDER BY created_at DESC LIMIT 3",
            (cluster_id,),
        )

    async def campaign(self, campaign_id: int) -> dict[str, Any] | None:
        rows = await self._all("SELECT * FROM campaigns WHERE id = %s AND status = 'done'", (campaign_id,))
        return rows[0] if rows else None

    async def campaign_for_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        rows = await self._all(
            "SELECT id, title, kind, verdict, cardinality(cluster_ids) AS clusters, accounts FROM campaigns "
            "WHERE status = 'done' AND %s = ANY(cluster_ids) AND created_at > now() - interval '24 hours' "
            "ORDER BY created_at DESC LIMIT 1",
            (cluster_id,),
        )
        return rows[0] if rows else None

    async def accounts_by_hash(self, hashes: list[str]) -> dict[str, dict[str, Any]]:
        if not hashes:
            return {}
        rows = await self._all(
            "SELECT account_hash, handle, display_name, status, "
            "coalesce(on_public_list, false) AS on_public_list FROM accounts WHERE account_hash = ANY(%s)",
            (hashes,),
        )
        return {r["account_hash"]: r for r in rows}

    async def live_findings(self) -> dict[str, Any]:
        """Raw material for the 'live findings' numbers: everything in the database (the 72-hour
        window), flagged = tier suspicious or watch."""
        totals = (
            await self._all(
                """
                SELECT min(c.first_seen) AS since,
                       count(DISTINCT c.id) AS flagged_clusters,
                       count(DISTINCT m.account_hash) AS flagged_accounts,
                       count(DISTINCT m.account_hash) FILTER (WHERE a.on_public_list) AS listed_accounts,
                       count(DISTINCT m.account_hash) FILTER (WHERE a.status = 'unavailable')
                           AS removed_accounts,
                       count(DISTINCT c.id) FILTER (
                           WHERE c.signals @> '{"signals": [{"name": "telegram_first", "fired": true}]}'
                       ) AS telegram_first_clusters
                FROM clusters c
                JOIN cluster_members m ON m.cluster_id = c.id
                LEFT JOIN accounts a ON a.account_hash = m.account_hash
                WHERE c.tier IN ('suspicious', 'watch')
                """,
                (),
            )
        )[0]
        handles = await self._all(
            """
            SELECT DISTINCT a.handle FROM clusters c
            JOIN cluster_members m ON m.cluster_id = c.id
            JOIN accounts a ON a.account_hash = m.account_hash
            WHERE c.tier IN ('suspicious', 'watch') AND a.handle IS NOT NULL
              AND NOT coalesce(a.on_public_list, false)
            """,
            (),
        )
        domains = await self._all(
            """
            SELECT DISTINCT d->>0 AS domain FROM clusters c, jsonb_array_elements(c.domains) d
            WHERE c.tier IN ('suspicious', 'watch')
            UNION SELECT label FROM clusters WHERE kind = 'domain' AND tier IN ('suspicious', 'watch')
            """,
            (),
        )
        investigations = (
            await self._all(
                """
                SELECT count(DISTINCT cluster_id) AS investigated,
                       count(DISTINCT cluster_id) FILTER (WHERE verdict = 'staged') AS staged
                FROM investigations WHERE status = 'done'
                """,
                (),
            )
        )[0]
        recycled = (
            await self._all(
                "SELECT count(*) AS recycled_pictures, "
                "count(*) FILTER (WHERE supports IN ('earlier', 'neither')) AS recycled_mismatch "
                "FROM image_reuse WHERE status = 'done'",
                (),
            )
        )[0]
        campaigns = (
            await self._all(
                "SELECT count(DISTINCT campaign_key) AS campaigns, "
                "count(DISTINCT campaign_key) FILTER (WHERE verdict = 'staged') AS staged_campaigns "
                "FROM campaigns WHERE status = 'done'",
                (),
            )
        )[0]
        return {
            **totals,
            **investigations,
            **campaigns,
            **recycled,
            "unlisted_handles": [r["handle"] for r in handles],
            "domains": [r["domain"] for r in domains if r["domain"]],
        }

    async def review_categories(self) -> dict[str, int]:
        """Latest decision per cluster: how many confirmed clusters of each kind."""
        rows = await self._all(
            """
            WITH latest AS (
                SELECT DISTINCT ON (cluster_id) cluster_id, decision, category
                FROM reviews ORDER BY cluster_id, created_at DESC
            )
            SELECT coalesce(category, 'unspecified') AS category, count(*) AS n
            FROM latest WHERE decision = 'confirm' GROUP BY 1
            """,
            (),
        )
        return {row["category"]: int(row["n"]) for row in rows}

    async def latest_eval_run(self) -> dict[str, Any] | None:
        rows = await self._all(
            "SELECT name, created_at, result FROM eval_runs ORDER BY created_at DESC LIMIT 1", ()
        )
        return rows[0] if rows else None

    async def review_counts(
        self, sampled_only: bool = False, topic_only: bool = False
    ) -> list[tuple[str | None, str, str | None, int]]:
        """Latest decision per cluster; `topic_only`: only groups on our topics (factslens.common.topics)."""
        rows = await self._all(
            """
            WITH latest AS (
                SELECT DISTINCT ON (r.cluster_id) r.cluster_id, r.decision, r.tier_at, r.verdict_at
                FROM reviews r JOIN clusters c ON c.id = r.cluster_id
                WHERE (r.sampled OR NOT %(sampled_only)s)
                  AND (NOT %(topic_only)s OR concat_ws(' ', c.label, (
                    SELECT concat_ws(' ', i.report->>'summary', i.report->>'narrative')
                    FROM investigations i WHERE i.cluster_id = c.id AND i.status = 'done'
                    ORDER BY i.created_at DESC LIMIT 1)) ~* %(topic)s)
                ORDER BY r.cluster_id, r.created_at DESC
            )
            SELECT tier_at, decision, verdict_at, count(*) AS n FROM latest GROUP BY 1, 2, 3
            """,
            {"sampled_only": sampled_only, "topic_only": topic_only, "topic": topics.SQL_PATTERN},
        )
        return [(r["tier_at"], r["decision"], r["verdict_at"], int(r["n"])) for r in rows]

    async def nemotron_stats(self) -> dict[str, Any]:
        versions = await self._all(
            """
            SELECT prompt_version, count(*) AS investigations,
                   count(*) FILTER (WHERE status = 'done') AS done,
                   count(*) FILTER (WHERE verdict = 'staged') AS staged,
                   count(*) FILTER (WHERE verdict = 'organic') AS organic,
                   count(*) FILTER (WHERE verdict = 'insufficient_evidence') AS insufficient,
                   round(avg((evidence->'grounding'->>'share')::numeric), 3) AS grounding,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS latency_p50_ms,
                   round(avg(calls), 1) AS calls_per_investigation
            FROM investigations GROUP BY prompt_version ORDER BY prompt_version
            """,
            (),
        )
        calls = await self._all(
            """
            SELECT count(*) AS calls, count(*) FILTER (WHERE status <> 'ok') AS errors,
                   sum(attempts - 1) AS retries,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS latency_p50_ms,
                   sum(prompt_tokens) AS prompt_tokens, sum(completion_tokens) AS completion_tokens
            FROM model_calls WHERE at > now() - interval '24 hours'
            """,
            (),
        )
        return {"versions": versions, "calls_24h": calls[0] if calls else {}}
