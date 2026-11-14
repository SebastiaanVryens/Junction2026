from __future__ import annotations

from typing import Any

from factslens.common.models import Event, EventKind, MediaKind, Platform
from factslens.ingest.jetstream import MAX_TEXT_CHARS, event_from_jetstream

DID = "did:plc:abc123"


def commit(collection: str, record: dict[str, Any], operation: str = "create") -> dict[str, Any]:
    return {
        "did": DID,
        "time_us": 1_763_000_000_000_000,
        "kind": "commit",
        "commit": {
            "operation": operation,
            "collection": collection,
            "rkey": "3kabc",
            "cid": "bafy",
            "record": record,
        },
    }


def test_post_with_facets_images_and_reply() -> None:
    raw = commit(
        "app.bsky.feed.post",
        {
            "$type": "app.bsky.feed.post",
            "text": "Read this #Breaking https://example.com/a",
            "createdAt": "2026-11-13T20:00:00.000Z",
            "langs": ["en"],
            "facets": [
                {"features": [{"$type": "app.bsky.richtext.facet#tag", "tag": "Breaking"}]},
                {"features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://example.com/a"}]},
                {"features": [{"$type": "app.bsky.richtext.facet#mention", "did": "did:plc:other"}]},
            ],
            "reply": {
                "parent": {"uri": "at://did:plc:p/app.bsky.feed.post/1"},
                "root": {"uri": "at://x/y/z"},
            },
            "embed": {
                "$type": "app.bsky.embed.images",
                "images": [
                    {
                        "alt": "a chart",
                        "image": {"$type": "blob", "ref": {"$link": "bafimg1"}, "mimeType": "image/jpeg"},
                    }
                ],
            },
        },
    )
    event = event_from_jetstream(raw)
    assert event is not None
    assert event.id == f"at://{DID}/app.bsky.feed.post/3kabc"
    assert (event.platform, event.kind, event.author) == (Platform.BLUESKY, EventKind.POST, DID)
    assert event.hashtags == ("breaking",)
    assert event.links == ("https://example.com/a",)
    assert event.mentions == ("did:plc:other",)
    assert event.reply_to == "at://did:plc:p/app.bsky.feed.post/1"
    assert [(m.kind, m.ref, m.mime, m.alt) for m in event.media] == [
        (MediaKind.IMAGE, "bafimg1", "image/jpeg", "a chart")
    ]


def test_link_card_counts_as_link_once() -> None:
    raw = commit(
        "app.bsky.feed.post",
        {
            "text": "x",
            "facets": [
                {"features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://t.me/chan/1"}]}
            ],
            "embed": {
                "$type": "app.bsky.embed.external",
                "external": {"uri": "https://t.me/chan/1", "title": "t"},
            },
        },
    )
    event = event_from_jetstream(raw)
    assert event is not None
    assert event.links == ("https://t.me/chan/1",)
    assert [m.kind for m in event.media] == [MediaKind.EXTERNAL]


def test_quote_with_video() -> None:
    raw = commit(
        "app.bsky.feed.post",
        {
            "text": "look",
            "embed": {
                "$type": "app.bsky.embed.recordWithMedia",
                "record": {"record": {"uri": "at://did:plc:q/app.bsky.feed.post/9", "cid": "c"}},
                "media": {
                    "$type": "app.bsky.embed.video",
                    "video": {"ref": {"$link": "bafvid"}, "mimeType": "video/mp4"},
                },
            },
        },
    )
    event = event_from_jetstream(raw)
    assert event is not None
    assert event.quote_of == "at://did:plc:q/app.bsky.feed.post/9"
    assert [(m.kind, m.ref) for m in event.media] == [(MediaKind.VIDEO, "bafvid")]


def test_repost_and_follow_subjects() -> None:
    repost = event_from_jetstream(
        commit(
            "app.bsky.feed.repost", {"subject": {"uri": "at://did:plc:o/app.bsky.feed.post/7", "cid": "c"}}
        )
    )
    follow = event_from_jetstream(commit("app.bsky.graph.follow", {"subject": "did:plc:target"}))
    assert repost is not None and repost.kind is EventKind.REPOST
    assert repost.subject == "at://did:plc:o/app.bsky.feed.post/7"
    assert follow is not None and follow.subject == "did:plc:target"


def test_ignores_what_we_do_not_ingest() -> None:
    assert event_from_jetstream({"did": DID, "time_us": 1, "kind": "identity"}) is None
    assert event_from_jetstream(commit("app.bsky.feed.post", {"text": "x"}, operation="delete")) is None
    assert event_from_jetstream(commit("app.bsky.actor.profile", {"displayName": "x"})) is None
    assert event_from_jetstream(commit("app.bsky.feed.repost", {"subject": "not-an-object"})) is None


def test_malformed_records_do_not_crash() -> None:
    raw = commit(
        "app.bsky.feed.post",
        {
            "text": 42,
            "langs": "en",
            "facets": [None, {"features": "nope"}, {"features": [{"$type": "app.bsky.richtext.facet#link"}]}],
            "embed": {"$type": "app.bsky.embed.images", "images": [{"image": "not-a-blob"}, 7]},
            "reply": "nope",
        },
    )
    event = event_from_jetstream(raw)
    assert event is not None
    assert (event.text, event.langs, event.links, event.media, event.reply_to) == ("", (), (), (), None)


def test_text_is_capped_and_json_round_trips() -> None:
    event = event_from_jetstream(commit("app.bsky.feed.post", {"text": "é" * (MAX_TEXT_CHARS + 50)}))
    assert event is not None and len(event.text) == MAX_TEXT_CHARS
    assert Event.from_json(event.to_json()) == event
    assert b'"synthetic"' not in event.to_json()  # defaults are omitted to keep messages small


def test_bridged_post_keeps_its_origin() -> None:
    raw = commit(
        "app.bsky.feed.post",
        {
            "text": "Cut off mid sen",
            "bridgyOriginalUrl": "https://mast.example.com/@user/123",
            "bridgyOriginalText": "<p>Cut off mid sentence, longer on Mastodon</p>",
        },
    )
    event = event_from_jetstream(raw)
    native = event_from_jetstream(commit("app.bsky.feed.post", {"text": "hi", "bridgyOriginalUrl": 5}))
    assert event is not None and native is not None
    assert event.origin_url == "https://mast.example.com/@user/123"
    assert event.origin_text == "<p>Cut off mid sentence, longer on Mastodon</p>"
    assert (native.origin_url, native.origin_text) == (None, None)


# --- reconnects and server rotation -------------------------------------------------------

import asyncio  # noqa: E402
import json  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from urllib.parse import parse_qs, urlsplit  # noqa: E402

from factslens.ingest.jetstream import JetstreamSource  # noqa: E402

STREAM = [1_000_000 * n for n in range(1, 41)]  # one event per second of stream time


def message(time_us: int) -> str:
    return json.dumps(commit("app.bsky.feed.post", {"text": f"t{time_us}"}) | {"time_us": time_us})


class FakeServers:
    """Each server replays STREAM from the requested cursor; a 'stuck' server goes silent
    once it reaches `stuck_at` (like Bluesky's us-east servers on 14 Nov 2026)."""

    def __init__(self, stuck_at: dict[str, int]) -> None:
        self.stuck_at = stuck_at
        self.connections: list[tuple[str, int | None]] = []

    @asynccontextmanager
    async def connect(self, url: str, **_: object) -> AsyncIterator[object]:
        parts = urlsplit(url)
        host = parts.netloc
        cursor_values = parse_qs(parts.query).get("cursor")
        cursor = int(cursor_values[0]) if cursor_values else None
        self.connections.append((host, cursor))
        pending = [t for t in STREAM if cursor is None or t >= cursor]
        stuck = self.stuck_at.get(host)
        if stuck is not None and cursor is not None and cursor > stuck:
            stuck = None  # only replaying *through* the stuck point stalls

        class Socket:
            async def recv(self) -> str:
                if not pending or (stuck is not None and pending[0] > stuck):
                    await asyncio.sleep(3600)
                return message(pending.pop(0))

        yield Socket()


async def take(source: JetstreamSource, cursor: int | None, until: int) -> list[int]:
    seen: list[int] = []
    async for item in source.items(cursor):
        seen.append(item.cursor)
        if item.cursor >= until:
            return seen
    return seen


def source(servers: FakeServers, hosts: list[str], **kwargs: int) -> JetstreamSource:
    return JetstreamSource(
        [f"wss://{h}/subscribe" for h in hosts],
        idle_timeout_s=0.05,
        max_backoff_s=0.01,
        rewind_us=kwargs.get("rewind_us", 2_000_000),
        skip_us=kwargs.get("skip_us", 5_000_000),
        connector=servers.connect,
    )


async def test_rotates_past_a_stuck_server_without_drifting_back() -> None:
    servers = FakeServers(stuck_at={"east1": 5_000_000, "east2": 5_000_000})
    src = source(servers, ["east1", "east2", "west1"])
    seen = await asyncio.wait_for(take(src, 1_000_000, until=20_000_000), timeout=5)
    assert seen[-1] == 20_000_000
    assert [host for host, _ in servers.connections] == ["east1", "east2", "west1"]
    # Every retry resumes 2 s before the newest event, not 2 s further back each time.
    assert [cursor for _, cursor in servers.connections] == [1_000_000, 3_000_000, 3_000_000]
    assert src.skipped_us == 0
    assert set(range(1_000_000, 20_000_001, 1_000_000)) <= set(seen)  # no gap


async def test_skips_ahead_when_every_server_is_stuck() -> None:
    servers = FakeServers(stuck_at={"a": 5_000_000, "b": 5_000_000})
    src = source(servers, ["a", "b"])
    seen = await asyncio.wait_for(take(src, 1_000_000, until=12_000_000), timeout=5)
    assert seen[-1] == 12_000_000
    assert src.skipped_us == 5_000_000
    assert 6_000_000 not in seen and 10_000_000 in seen  # the stuck stretch is skipped, and logged
