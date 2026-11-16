"""The paper edition's memory: the compared stories, in one JSON file instead of a database.

Stories are news about outlets and channels, not about private people, so they are kept for
KEEP_DAYS (the full edition's 72-hour limit is for posts of individual accounts)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

KEEP_DAYS = 7
FIRST_ID = 1000  # well above the full edition's story numbers, so old links never point at a new story


@dataclass
class PaperState:
    path: Path
    stories: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = FIRST_ID

    @classmethod
    def load(cls, path: Path) -> PaperState:
        if not path.is_file():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, list(data.get("stories") or []), int(data.get("next_id") or FIRST_ID))

    def save(self) -> None:
        """Write atomically: a crash mid-write never leaves a broken file behind."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"next_id": self.next_id, "stories": self.stories}, default=str), "utf-8")
        os.replace(tmp, self.path)

    def add(self, story: dict[str, Any], now: datetime) -> int:
        story_id = self.next_id
        self.next_id += 1
        self.stories.append({**story, "id": story_id, "created_at": now.isoformat(), "status": "done"})
        return story_id

    def since(self, hours: float, now: datetime) -> list[dict[str, Any]]:
        """Stories compared in the last `hours`, newest first."""
        cutoff = now - timedelta(hours=hours)
        recent = [s for s in self.stories if created(s) >= cutoff]
        return sorted(recent, key=created, reverse=True)

    def prune(self, now: datetime) -> list[int]:
        """Forget stories older than KEEP_DAYS; returns their ids (their pages are removed too)."""
        cutoff = now - timedelta(days=KEEP_DAYS)
        old = [s["id"] for s in self.stories if created(s) < cutoff]
        self.stories = [s for s in self.stories if created(s) >= cutoff]
        return old


def created(story: dict[str, Any]) -> datetime:
    value = story["created_at"]
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
