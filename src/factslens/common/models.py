"""The one event format every source is converted to and every service consumes.

A source adapter (Bluesky Jetstream today; Telegram channels and Pravda sites next) turns its
platform's records into `Event`s, so the detector never sees platform-specific shapes.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Platform(StrEnum):
    BLUESKY = "bluesky"
    TELEGRAM = "telegram"
    PRAVDA = "pravda"


class EventKind(StrEnum):
    POST = "post"
    REPOST = "repost"
    LIKE = "like"
    FOLLOW = "follow"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    EXTERNAL = "external"  # link card


class Media(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: MediaKind
    ref: str  # Bluesky blob CID for images/video; URL for link cards and other platforms
    mime: str | None = None
    alt: str | None = None


class Event(BaseModel):
    """One public action by one account. Field names are short because we store ~9M of these a day."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # globally unique: an at:// URI on Bluesky, a URL elsewhere
    platform: Platform
    kind: EventKind
    author: str  # Bluesky DID, Telegram channel name, or site domain
    time_us: int  # when the source saw it (microseconds since the epoch); the ingest cursor
    created_at: str | None = None  # timestamp claimed by the author; can be wrong or forged
    text: str = ""
    langs: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()  # lower-cased, without '#'
    mentions: tuple[str, ...] = ()
    reply_to: str | None = None  # id of the parent post
    quote_of: str | None = None  # id of the quoted post
    subject: str | None = None  # target of a repost/like (post id) or follow (account)
    media: tuple[Media, ...] = ()
    # Set when the post was bridged from another network (Bridgy Fed copies Mastodon posts to
    # Bluesky): where it was first published, and its text there (HTML, not cut to 300 characters).
    origin_url: str | None = None
    origin_text: str | None = None
    synthetic: bool = False  # true for red-team test campaigns; these never leave our system

    def to_json(self) -> bytes:
        return self.model_dump_json(exclude_defaults=True).encode()

    @classmethod
    def from_json(cls, data: bytes | str) -> Event:
        return cls.model_validate_json(data)
