"""Where an ingest job remembers how far it got, so a restart resumes instead of losing data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

import psycopg


class CursorStore(Protocol):
    async def load(self, name: str) -> int | None: ...

    async def save(self, name: str, cursor: int) -> None: ...


class FileCursorStore:
    """One small JSON file per job; written atomically."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self._directory / f"{name}.cursor.json"

    async def load(self, name: str) -> int | None:
        try:
            value = json.loads(self._path(name).read_text(encoding="utf-8")).get("cursor")
        except (FileNotFoundError, ValueError):
            return None
        return value if isinstance(value, int) else None

    async def save(self, name: str, cursor: int) -> None:
        path = self._path(name)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({"cursor": cursor}), encoding="utf-8")
        os.replace(temp, path)


class PostgresCursorStore:
    """Cursor in the `ingest_cursor` table (see migrations/001_init.sql)."""

    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self._connection = connection

    async def load(self, name: str) -> int | None:
        cursor = await self._connection.execute("SELECT cursor FROM ingest_cursor WHERE source = %s", (name,))
        row = await cursor.fetchone()
        return int(str(row[0])) if row else None

    async def save(self, name: str, cursor: int) -> None:
        await self._connection.execute(
            """
            INSERT INTO ingest_cursor (source, cursor, updated_at) VALUES (%s, %s, now())
            ON CONFLICT (source) DO UPDATE SET cursor = EXCLUDED.cursor, updated_at = now()
            """,
            (name, cursor),
        )
