"""The paper edition: its state file, its data for the story pages, and the static build."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from test_api import STORY

from factslens.enrich.lists import KnownLists
from factslens.investigator import stories
from factslens.paper.build import PaperSite
from factslens.paper.repository import StateRepository
from factslens.paper.state import (
    FIRST_ID,
    KEEP_DAYS,
    PaperState,
    clean_report,
    from_full_edition,
    without_social,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def stored(**extra: Any) -> dict[str, Any]:
    """A compared story as the round stores it (JSON only)."""
    return {k: v for k, v in STORY.items() if k not in ("id", "created_at")} | extra


def test_the_state_survives_a_restart_and_forgets_old_stories(tmp_path: Path) -> None:
    path = tmp_path / "paper" / "state.json"
    state = PaperState.load(path)
    assert state.stories == [] and state.next_id == FIRST_ID
    old = state.add(stored(title="Old"), NOW - timedelta(days=KEEP_DAYS, hours=1))
    new = state.add(stored(title="New"), NOW - timedelta(hours=2))
    state.save()
    again = PaperState.load(path)
    assert [s["title"] for s in again.since(24, NOW)] == ["New"] and again.next_id == new + 1
    assert again.prune(NOW) == [old] and [s["id"] for s in again.stories] == [new]


def test_the_story_pages_read_the_state(tmp_path: Path) -> None:
    state = PaperState(tmp_path / "state.json")
    first = state.add(stored(title="First"), NOW - timedelta(hours=3))
    second = state.add(stored(title="Second"), NOW - timedelta(hours=1))
    repo = StateRepository(state)
    latest = asyncio.run(repo.latest_stories(5))
    assert [s["id"] for s in latest] == [second, first] and isinstance(latest[0]["created_at"], datetime)
    assert asyncio.run(repo.story(first))["title"] == "First"  # type: ignore[index]
    assert asyncio.run(repo.story(1)) is None and asyncio.run(repo.tier_counts(24)) == {}


def test_the_build_writes_the_paper_without_the_tools(tmp_path: Path) -> None:
    state = PaperState(tmp_path / "state.json")
    now = datetime.now(UTC)
    story_id = state.add(stored(), now - timedelta(hours=1))
    site = tmp_path / "site"
    with PaperSite(state, site, KnownLists(), find_picture=lambda urls: None) as paper:
        written = paper.build(now)
    assert written == 5  # front page, stories, how we work, robots.txt, one story
    front = (site / "index.html").read_text(encoding="utf-8")
    assert "What is the truth?" in front and "1 story compared in the last 24 hours" in front
    assert 'href="/lens"' not in front and 'href="/network"' not in front and 'href="/how-we-work"' in front
    page = (site / "stories" / str(story_id) / "index.html").read_text(encoding="utf-8")
    assert "Who is spreading which version" not in page and "Sources" in page
    assert "How We Work" in (site / "how-we-work" / "index.html").read_text(encoding="utf-8")
    assert (site / "404.html").is_file() and (site / "static" / "favicon.svg").is_file()
    with PaperSite(state, site, KnownLists(), find_picture=lambda urls: None) as paper:
        paper.remove([story_id])
    assert not (site / "stories" / str(story_id)).exists()


def test_a_round_can_pick_more_stories_and_leaves_social_media_out() -> None:
    assert "{count}" in stories.PICK_PROMPT and stories.COUNT_WORDS[4] == "four"
    pick = stories.StoryPick.model_validate({"title": "T", "refs": ["H1"], "keywords": [], "searches": []})
    headline = stories.Headline("TASS", "state_media", NOW, "A headline", "https://tass.example/1")
    pack = stories.build_story_pack(pick, [headline], [], None, [])
    assert "does not watch social media" in pack.text and "(S)" not in pack.text
    assert "Social-media clusters" in stories.build_story_pack(pick, [headline], [], [], []).text


def test_stories_from_the_full_edition_come_without_their_social_media_parts(tmp_path: Path) -> None:
    row = STORY | {"id": 225, "created_at": NOW, "cluster_ids": ["text:abc"]}
    report: dict[str, Any] = dict(STORY["report"])  # type: ignore[call-overload]
    report["amplification"] = [{"ref": "S1", "pushes": "both"}]
    report["summary"] = "TASS says X. Social clusters do not push this story."
    row["report"] = report
    row["evidence"] = {"headlines": {}, "clusters": {"S1": "text:abc"}}
    story = from_full_edition(row)
    assert story["report"]["amplification"] == [] and "clusters" not in story["evidence"]
    state = PaperState(tmp_path / "state.json")
    assert state.put(story) and not state.put(story) and state.next_id == FIRST_ID  # 225 + 1 < FIRST_ID
    shown = asyncio.run(StateRepository(state).story(225))
    assert shown is not None and shown["report"]["summary"] == "TASS says X."
    assert without_social("A. B groups of accounts repeat it. C?") == "A. C?"


def test_versions_and_points_about_groups_of_accounts_are_left_out() -> None:
    report = {
        "summary": "TASS says X. A social cluster repeats it.",
        "versions": [{"who": "TASS (state media)", "claim": "X", "refs": ["H1"]},
                     {"who": "Social clusters", "claim": "Y", "refs": ["S1"]}],
        "contradictions": [
            {"about": "numbers", "detail": "RT says 5; social clusters say 50.", "refs": ["H1"]}
        ],
        "agreements": [{"point": "All say Z. A social cluster adds W.", "refs": ["N1"]}],
    }  # fmt: skip
    clean = clean_report(report)
    assert clean["summary"] == "TASS says X." and [v["who"] for v in clean["versions"]] == [
        "TASS (state media)"
    ]
    assert clean["contradictions"] == [] and clean["agreements"][0]["point"] == "All say Z."
