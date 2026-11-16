"""The paper edition's memory: the compared stories, in one JSON file instead of a database.

Stories are news about outlets and channels, not about private people, so they are kept for
KEEP_DAYS (the full edition's 72-hour limit is for posts of individual accounts)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

KEEP_DAYS = 7
FIRST_ID = 1000  # well above the full edition's story numbers, so old links never point at a new story
# A sentence about groups of accounts, which the paper edition does not watch.
# ...or names one by its evidence id ("S3 also cites ..."), which the pages print as "a social cluster".
SOCIAL = re.compile(
    r"cluster|groups? of accounts|social[- ]media (?:groups?|accounts)|(?-i:\bS\d{1,2}\b)", re.IGNORECASE
)


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

    def put(self, story: dict[str, Any]) -> bool:
        """Keep a story under its own id (an import); False when that id is already here."""
        if any(s["id"] == story["id"] for s in self.stories):
            return False
        self.stories.append(story)
        self.next_id = max(self.next_id, int(story["id"]) + 1)
        return True

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


# A period after these does not end a sentence ("Dec. 1", "Mr. Peskov", "U.S. troops").
ABBREVIATION = re.compile(
    r"(?:\b(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Mr|Mrs|Ms|Dr|St|No|vs|approx|e\.g|i\.e)"
    r"|\b[A-Z](?:\.[A-Z])+)\.$"
)


def sentences(text: str) -> list[str]:
    parts: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", text or ""):
        if parts and ABBREVIATION.search(parts[-1]):
            parts[-1] += " " + part
        else:
            parts.append(part)
    return parts


def without_social(text: str) -> str:
    """'Russia says X. Social clusters do not push this story.' -> 'Russia says X.'"""
    return " ".join(s for s in sentences(text) if not SOCIAL.search(s))


def from_full_edition(row: dict[str, Any]) -> dict[str, Any]:
    """A story compared by the full edition, without its social-media parts: the paper edition
    shows no groups of accounts."""
    report = dict(row.get("report") or {})
    report["amplification"] = []
    evidence = {k: v for k, v in (row.get("evidence") or {}).items() if k != "clusters"}
    moment = row["created_at"]
    return {
        **{
            k: row.get(k)
            for k in (
                "id",
                "title",
                "story_status",
                "model",
                "prompt_version",
                "calls",
                "prompt_tokens",
                "completion_tokens",
                "latency_ms",
            )
        },
        "created_at": moment.isoformat() if isinstance(moment, datetime) else moment,
        "status": "done",
        "cluster_ids": [],
        "report": report,
        "evidence": evidence,
    }


TEXT_KEYS_IGNORED = {"ref", "refs", "kind", "about", "pushes", "language"}


def clean_report(report: dict[str, Any]) -> dict[str, Any]:
    """A report without its social-media parts: versions told by groups of accounts go, sentences about
    them go, and a point left with no words goes. The paper edition watches no social media."""
    out: dict[str, Any] = {}
    for key, value in report.items():
        if isinstance(value, str):
            out[key] = without_social(value)
        elif isinstance(value, list):
            items: list[Any] = []
            for item in value:
                if isinstance(item, dict):
                    if SOCIAL.search(str(item.get("who") or "")):
                        continue  # a version told by a group of accounts
                    item = {k: without_social(v) if isinstance(v, str) else v for k, v in item.items()}
                    if not any(
                        isinstance(v, str) and v.strip()
                        for k, v in item.items()
                        if k not in TEXT_KEYS_IGNORED
                    ):
                        continue
                elif isinstance(item, str):
                    item = without_social(item)
                    if not item:
                        continue
                items.append(item)
            out[key] = items
        else:
            out[key] = value
    return out
