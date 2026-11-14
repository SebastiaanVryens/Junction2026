from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from factslens.common.models import Event, EventKind, Platform
from factslens.enrich.profiles import FetchedProfile
from factslens.eval.redteam import Campaign, CampaignPost, CampaignSet, Persona, account_id, to_events
from factslens.eval.run import ProfileCache, recording_files, run
from factslens.ingest.sinks import JsonlSink

START = datetime(2026, 11, 13, 15, tzinfo=UTC)
TEXT = "BREAKING: Velmora water plant poisoned by the ministry, officials hide the truth from families"


def campaign(difficulty: str = "simple") -> Campaign:
    personas = [
        Persona(bio="", account_age_days=3, followers=1, follows=0, has_avatar=False) for _ in range(8)
    ]
    posts = [
        CampaignPost(
            persona=i,
            minute=i * 0.3,
            text=TEXT[: 70 + i * 3],
            lang="en",
            link="https://velmora-truth.example/a",
        )
        for i in range(8)
    ]
    return Campaign(
        difficulty=difficulty,  # type: ignore[arg-type]
        narrative="A fictional poisoning scandal.",
        tactics=["copy-paste"],
        techniques=["fear_appeal"],
        personas=personas,
        posts=posts,
    )


def test_campaign_links_must_be_reserved_example_domains() -> None:
    with pytest.raises(ValueError):
        CampaignPost(persona=0, minute=1, text="x", lang="en", link="https://real-news.com/a")


def test_synthetic_events_are_marked_and_cannot_be_real_accounts() -> None:
    events = to_events(campaign(), 3, 1_000_000)
    assert all(e.synthetic for e in events)
    assert events[0].author == account_id(3, 0) == "did:plc:synthetic003x000"
    assert [e.time_us for e in events] == sorted(e.time_us for e in events)


class FakeProfiles:
    """Real accounts in the recording look like ordinary established users."""

    calls = 0

    def fetch(self, dids: list[str]) -> dict[str, FetchedProfile]:
        FakeProfiles.calls += 1
        return {
            d: FetchedProfile(
                did=d,
                handle=f"{d[-6:]}.bsky.social",
                display_name=None,
                description=f"Person {hashlib.sha1(d.encode()).hexdigest()} writes about their life",
                created_at=START - timedelta(days=500 + int(d[-3:], 16) % 300),
                followers=200,
                follows=150,
                posts=1000,
                has_avatar=True,
                has_banner=True,
                labels=(),
            )
            for d in dids
        }


def write_recording(directory: Path) -> None:
    sink = JsonlSink(directory)
    events = []
    for n in range(400):  # ordinary chatter, every account in its own words
        unique = hashlib.sha256(str(n).encode()).hexdigest()
        events.append(
            Event(
                id=f"at://did:plc:real{n:03x}/app.bsky.feed.post/{n}",
                platform=Platform.BLUESKY,
                kind=EventKind.POST,
                author=f"did:plc:real{n:03x}",
                time_us=int((START + timedelta(seconds=n * 9)).timestamp() * 1e6),
                text=f"my own thoughts today {unique}",
            )
        )

    async def write() -> None:
        for event in events:
            await sink.write(event)
        await sink.close()

    asyncio.run(write())


def test_recording_files_selects_overlapping_hours(tmp_path: Path) -> None:
    for hour in (14, 15, 16):
        (tmp_path / f"events-20261113-{hour}.jsonl.gz").write_bytes(b"")
    names = [p.name for p in recording_files(tmp_path, START + timedelta(minutes=30), 1.0)]
    assert names == ["events-20261113-15.jsonl.gz", "events-20261113-16.jsonl.gz"]


def test_replay_catches_a_simple_campaign_without_flagging_real_chatter(tmp_path: Path) -> None:
    write_recording(tmp_path / "rec")
    campaigns = CampaignSet(prompt_version="test", model="test", generated_at=START, campaigns=[campaign()])
    cache = ProfileCache(tmp_path / "profiles.jsonl", FakeProfiles())  # type: ignore[arg-type]
    result = run(START, 1.0, campaigns, tmp_path / "rec", cache)

    row = result["campaigns"][0]
    assert row["best_tier"] == "suspicious" and row["coverage"] == 1.0
    assert row["minutes_to_candidate"] is not None and row["minutes_to_candidate"] < 5
    assert "new_accounts" in row["fired"] and "copy_paste" in row["fired"]
    assert result["campaign_recall"]["simple"]["suspicious"] == {"caught": 1, "n": 1, "share": 1.0}
    assert result["real_traffic"]["by_tier"].get("suspicious", 0) == 0
    assert result["run"]["synthetic_events"] == 8 and result["run"]["real_events"] == 400
    assert result["threshold_sweep"][0]["campaign_recall"] == 1.0
