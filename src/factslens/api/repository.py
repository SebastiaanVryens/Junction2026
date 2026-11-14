"""Read-only queries the web app needs, behind one interface so tests can use a fake."""

from __future__ import annotations

from typing import Any, Protocol

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from factslens.common import db

TIERS = ("suspicious", "watch", "pending", "declared_automation", "likely_organic")


class Repository(Protocol):
    async def statuses(self) -> dict[str, db.ServiceStatus]: ...

    async def tier_counts(self, hours: int) -> dict[str, int]: ...

    async def clusters(self, tier: str | None, hours: int, limit: int) -> list[dict[str, Any]]: ...

    async def cluster(self, cluster_id: str) -> dict[str, Any] | None: ...

    async def members(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def posts(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def related(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def investigation(self, cluster_id: str) -> dict[str, Any] | None: ...

    async def latest_eval_run(self) -> dict[str, Any] | None: ...

    async def review_counts(self) -> list[tuple[str | None, str, str | None, int]]: ...

    async def nemotron_stats(self) -> dict[str, Any]: ...

    async def reviews(self, cluster_id: str) -> list[dict[str, Any]]: ...

    async def add_review(
        self, cluster_id: str, decision: str, note: str | None, tier: str | None, verdict: str | None
    ) -> None: ...


class PostgresRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def _all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
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

    async def clusters(self, tier: str | None, hours: int, limit: int) -> list[dict[str, Any]]:
        return await self._all(
            """
            SELECT id, kind, label, first_seen, last_seen, accounts, events, score,
                   coalesce(tier, 'pending') AS tier, signals,
                   (SELECT i.verdict FROM investigations i
                    WHERE i.cluster_id = clusters.id AND i.status = 'done'
                    ORDER BY i.created_at DESC LIMIT 1) AS verdict
            FROM clusters
            WHERE last_seen > now() - make_interval(hours => %s)
              AND (%s::text IS NULL OR coalesce(tier, 'pending') = %s)
            ORDER BY score DESC NULLS LAST, last_seen DESC
            LIMIT %s
            """,
            (hours, tier, tier, limit),
        )

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
                   a.created_at, a.followers, a.follows, a.posts, a.has_avatar, a.status, a.labels
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
            SELECT p.post_id, p.posted_at, p.text, p.bridged, p.media, a.handle
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
            "SELECT created_at, decision, note, tier_at, verdict_at FROM reviews "
            "WHERE cluster_id = %s ORDER BY created_at DESC LIMIT 20",
            (cluster_id,),
        )

    async def add_review(
        self, cluster_id: str, decision: str, note: str | None, tier: str | None, verdict: str | None
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "INSERT INTO reviews (cluster_id, decision, note, tier_at, verdict_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (cluster_id, decision, note, tier, verdict),
            )

    async def latest_eval_run(self) -> dict[str, Any] | None:
        rows = await self._all(
            "SELECT name, created_at, result FROM eval_runs ORDER BY created_at DESC LIMIT 1", ()
        )
        return rows[0] if rows else None

    async def review_counts(self) -> list[tuple[str | None, str, str | None, int]]:
        rows = await self._all(
            """
            WITH latest AS (
                SELECT DISTINCT ON (cluster_id) cluster_id, decision, tier_at, verdict_at
                FROM reviews ORDER BY cluster_id, created_at DESC
            )
            SELECT tier_at, decision, verdict_at, count(*) AS n FROM latest GROUP BY 1, 2, 3
            """,
            (),
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
