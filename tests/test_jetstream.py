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
