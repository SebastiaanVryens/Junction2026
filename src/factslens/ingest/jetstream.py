"""Bluesky Jetstream source: the public firehose as JSON over WebSocket, with cursor resume.

Records on Bluesky are written by arbitrary clients, so every field is treated as untrusted:
anything with the wrong shape is dropped rather than passed on.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from pydantic import ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from factslens.common.models import Event, EventKind, Media, MediaKind, Platform
from factslens.ingest.source import SourceItem

log = logging.getLogger(__name__)

COLLECTIONS: dict[str, EventKind] = {
    "app.bsky.feed.post": EventKind.POST,
    "app.bsky.feed.repost": EventKind.REPOST,
    "app.bsky.feed.like": EventKind.LIKE,
    "app.bsky.graph.follow": EventKind.FOLLOW,
}
DEFAULT_COLLECTIONS = ("app.bsky.feed.post", "app.bsky.feed.repost")

MAX_TEXT_CHARS = 3000
MAX_LIST_ITEMS = 32
MAX_REF_CHARS = 2048


def _str(value: Any, limit: int = MAX_REF_CHARS) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> Sequence[Any]:
    return value[:MAX_LIST_ITEMS] if isinstance(value, list) else ()


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _blob(value: Any, kind: MediaKind, alt: Any = None) -> Media | None:
    blob = _dict(value)
    cid = _str(_dict(blob.get("ref")).get("$link"))
    if cid is None:
        return None
    return Media(kind=kind, ref=cid, mime=_str(blob.get("mimeType"), 100), alt=_str(alt, 1000))


def _media(embed: Mapping[str, Any]) -> tuple[list[Media], str | None, str | None]:
    """Returns (media, quoted post URI, link-card URL) for a post's `embed`."""
    media: list[Media] = []
    quote: str | None = None
    card: str | None = None
    kind = embed.get("$type")
    if kind == "app.bsky.embed.recordWithMedia":
        quote = _str(_dict(_dict(embed.get("record")).get("record")).get("uri"))
        embed = _dict(embed.get("media"))
        kind = embed.get("$type")
    if kind == "app.bsky.embed.record":
        quote = _str(_dict(embed.get("record")).get("uri"))
    elif kind == "app.bsky.embed.images":
        for image in _list(embed.get("images")):
            item = _blob(_dict(image).get("image"), MediaKind.IMAGE, _dict(image).get("alt"))
            if item:
                media.append(item)
    elif kind == "app.bsky.embed.video":
        item = _blob(embed.get("video"), MediaKind.VIDEO, embed.get("alt"))
        if item:
            media.append(item)
    elif kind == "app.bsky.embed.external":
        card = _str(_dict(embed.get("external")).get("uri"))
        if card:
            media.append(Media(kind=MediaKind.EXTERNAL, ref=card))
    return media, quote, card


def _post_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    links: list[str] = []
    hashtags: list[str] = []
    mentions: list[str] = []
    for facet in _list(record.get("facets")):
        for feature in _list(_dict(facet).get("features")):
            feature = _dict(feature)
            kind = feature.get("$type")
            if kind == "app.bsky.richtext.facet#link" and (uri := _str(feature.get("uri"))):
                links.append(uri)
            elif kind == "app.bsky.richtext.facet#tag" and (tag := _str(feature.get("tag"), 100)):
                hashtags.append(tag.lower())
            elif kind == "app.bsky.richtext.facet#mention" and (did := _str(feature.get("did"), 200)):
                mentions.append(did)
    hashtags += [t.lower() for tag in _list(record.get("tags")) if (t := _str(tag, 100))]

    media, quote, card = _media(_dict(record.get("embed")))
    if card:
        links.append(card)
    text = record.get("text")
    return {
        "text": text[:MAX_TEXT_CHARS] if isinstance(text, str) else "",
        "langs": tuple(lang for item in _list(record.get("langs")) if (lang := _str(item, 20))),
        "links": _unique(links),
        "hashtags": _unique(hashtags),
        "mentions": _unique(mentions),
        "reply_to": _str(_dict(_dict(record.get("reply")).get("parent")).get("uri")),
        "quote_of": quote,
        "media": tuple(media),
    }


def event_from_jetstream(raw: Mapping[str, Any]) -> Event | None:
    """Convert one Jetstream message to an Event. Returns None for anything we don't ingest."""
    if raw.get("kind") != "commit":
        return None
    commit = _dict(raw.get("commit"))
    if commit.get("operation") != "create":
        return None
    collection = commit.get("collection")
    kind = COLLECTIONS.get(collection) if isinstance(collection, str) else None
    did, rkey, time_us = _str(raw.get("did"), 200), _str(commit.get("rkey"), 200), raw.get("time_us")
    record = commit.get("record")
    if (
        kind is None
        or did is None
        or rkey is None
        or not isinstance(time_us, int)
        or not isinstance(record, dict)
    ):
        return None

    fields: dict[str, Any]
    if kind is EventKind.POST:
        fields = _post_fields(record)
    else:
        subject = record.get("subject")
        # Reposts and likes point at a post ({uri, cid}); follows point at an account (a DID string).
        target = _str(subject, 200) if kind is EventKind.FOLLOW else _str(_dict(subject).get("uri"))
        if target is None:
            return None
        fields = {"subject": target}
    try:
        return Event(
            id=f"at://{did}/{collection}/{rkey}",
            platform=Platform.BLUESKY,
            kind=kind,
            author=did,
            time_us=time_us,
            created_at=_str(record.get("createdAt"), 64),
            **fields,
        )
    except ValidationError:
        return None


class JetstreamSource:
    """Yields every Jetstream message as a SourceItem, reconnecting forever.

    Delivery is at-least-once: after a reconnect we rewind a few seconds, so consumers must
    tolerate duplicates (events carry a unique `id`).
    """

    name = "jetstream"

    def __init__(
        self,
        url: str,
        collections: Sequence[str] = DEFAULT_COLLECTIONS,
        *,
        rewind_us: int = 5_000_000,
        idle_timeout_s: float = 30.0,
        max_backoff_s: float = 30.0,
    ) -> None:
        unknown = [c for c in collections if c not in COLLECTIONS]
        if unknown:
            raise ValueError(f"unsupported collections: {unknown}")
        self._url = url
        self._collections = tuple(collections)
        self._rewind_us = rewind_us
        self._idle_timeout_s = idle_timeout_s
        self._max_backoff_s = max_backoff_s
        self.reconnects = 0

    def _connect_url(self, cursor: int | None) -> str:
        params = [("wantedCollections", c) for c in self._collections]
        if cursor is not None:
            params.append(("cursor", str(cursor)))
        return f"{self._url}?{urlencode(params)}"

    async def items(self, cursor: int | None) -> AsyncIterator[SourceItem]:
        backoff = 1.0
        while True:
            try:
                # Pings are off: during a fast replay the server's backlog can starve the pong and
                # kill a healthy connection. The firehose never goes quiet, so silence means "dead".
                async with connect(self._connect_url(cursor), max_size=2**22, ping_interval=None) as ws:
                    log.info("jetstream connected", extra={"cursor": cursor, "reconnects": self.reconnects})
                    while True:
                        message = await asyncio.wait_for(ws.recv(), timeout=self._idle_timeout_s)
                        try:
                            raw = json.loads(message)
                        except ValueError:
                            continue
                        time_us = raw.get("time_us") if isinstance(raw, dict) else None
                        if not isinstance(time_us, int):
                            continue
                        cursor = time_us
                        backoff = 1.0
                        yield SourceItem(cursor=time_us, event=event_from_jetstream(raw))
            except (TimeoutError, OSError, WebSocketException) as error:
                self.reconnects += 1
                log.warning(
                    "jetstream disconnected; reconnecting",
                    extra={"error": f"{type(error).__name__}: {error}", "backoff_s": backoff},
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff_s)
                if cursor is not None:
                    cursor -= self._rewind_us
