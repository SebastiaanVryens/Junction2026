"""The story pages' data, read from the paper state instead of PostgreSQL.

Only what the front page, Contested Stories and a story page ask for; the rest of the full
edition (groups, campaigns, reviews) does not exist in the paper edition."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factslens.paper.state import PaperState, created


class StateRepository:
    def __init__(self, state: PaperState) -> None:
        self.state = state

    def _row(self, story: dict[str, Any]) -> dict[str, Any]:
        return {**story, "created_at": created(story)}

    async def latest_stories(self, limit: int) -> list[dict[str, Any]]:
        newest = self.state.since(hours=24 * 365, now=datetime.now(UTC))
        return [self._row(s) for s in newest if s.get("report")][:limit]

    async def story(self, story_id: int) -> dict[str, Any] | None:
        found = next((s for s in self.state.stories if s["id"] == story_id), None)
        return self._row(found) if found else None

    async def tier_counts(self, hours: int) -> dict[str, int]:
        return {}  # no groups of accounts in the paper edition
