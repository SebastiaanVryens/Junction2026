"""PostgreSQL access: connections, migrations, and the service-status table."""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from factslens.common.settings import REPO_ROOT, MissingSettingError, Settings

log = logging.getLogger(__name__)

MIGRATIONS_DIR = REPO_ROOT / "migrations"
_MIGRATION_LOCK_ID = 4_715_001  # arbitrary; stops two services migrating at the same time

type Connection = psycopg.AsyncConnection[tuple[Any, ...]]


def use_compatible_event_loop() -> None:
    """psycopg's async mode can't run on Windows' default (Proactor) event loop."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def database_url(settings: Settings) -> str:
    if settings.database_url is None:
        raise MissingSettingError("DATABASE_URL")
    return settings.database_url.get_secret_value()


async def connect(settings: Settings) -> Connection:
    return await psycopg.AsyncConnection.connect(database_url(settings), autocommit=True, connect_timeout=15)


async def migrate(connection: Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every not-yet-applied `*.sql` file in name order. Returns the names applied now."""
    applied_now: list[str] = []
    await connection.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_ID,))
    try:
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        cursor = await connection.execute("SELECT name FROM schema_migrations")
        done = {row[0] for row in await cursor.fetchall()}
        for path in sorted(directory.glob("*.sql")):
            if path.name in done:
                continue
            async with connection.transaction():
                await connection.execute(path.read_text(encoding="utf-8").encode())
                await connection.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
            log.info("migration applied", extra={"migration": path.name})
    finally:
        await connection.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))
    return applied_now


@dataclass(frozen=True)
class ServiceStatus:
    service: str
    updated_at: datetime
    data: dict[str, Any]


async def write_status(connection: Connection, service: str, data: dict[str, Any]) -> None:
    await connection.execute(
        """
        INSERT INTO service_status (service, updated_at, data) VALUES (%s, now(), %s)
        ON CONFLICT (service) DO UPDATE SET updated_at = now(), data = EXCLUDED.data
        """,
        (service, Jsonb(data)),
    )


async def read_statuses(connection: Connection) -> dict[str, ServiceStatus]:
    cursor = await connection.execute("SELECT service, updated_at, data FROM service_status")
    return {row[0]: ServiceStatus(row[0], row[1], row[2]) for row in await cursor.fetchall()}
