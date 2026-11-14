"""Telegram source: public channel pages (t.me/s/<channel>), no login, no API key.

Used to trace laundering chains: the Roska Bridge report documents Pravda content passing
through Telegram channels before it is posted on Bluesky with the source removed. We poll the
channels Pravda republishes most (CheckFirst's data) and feed their messages into the same
pipeline as Bluesky posts, so the detector can match the same text across platforms.

Politeness: one page per channel per poll (default every 5 minutes), with a user agent that
names the project. Only messages from the last 90 minutes are emitted.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

from factslens.common.models import Event, EventKind, Media, MediaKind, Platform
from factslens.ingest.source import SourceItem

log = logging.getLogger(__name__)

USER_AGENT = "FactsLens/0.1 (research on coordinated inauthentic behaviour; Junction 2026)"
MAX_AGE_US = 90 * 60 * 1_000_000
HASHTAG = re.compile(r"#(\w{2,64})")
BACKGROUND_URL = re.compile(r"background-image:url\('([^']+)'\)")
VOID_TAGS = frozenset({"br", "img", "hr", "input", "meta", "link", "source", "wbr", "area", "col"})


@dataclass
class TelegramMessage:
    post: str  # "channel/1234"
    time: datetime | None = None
    text_parts: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)

    @property
    def channel(self) -> str:
        return self.post.split("/", 1)[0].lower()

    @property
    def number(self) -> int:
        tail = self.post.rsplit("/", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    @property
    def text(self) -> str:
        # Like a browser: source whitespace collapses to one space; only <br> breaks a line.
        joined = "".join(self.text_parts)
        return re.sub(r" *\n *", "\n", joined).strip()


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[TelegramMessage] = []
        self._current: TelegramMessage | None = None
        self._text_depth = 0  # >0 while inside the message's own text block
        self._skip_depth = 0  # >0 while inside a quoted reply

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        classes = values.get("class", "")
        if tag == "div" and values.get("data-post"):
            self._current = TelegramMessage(post=values["data-post"])
            self.messages.append(self._current)
            self._text_depth = self._skip_depth = 0
            return
        message = self._current
        if message is None:
            return
        if self._skip_depth:
            if tag not in VOID_TAGS:
                self._skip_depth += 1
            return
        if "tgme_widget_message_reply" in classes:
            self._skip_depth = 1
            return
        if self._text_depth:
            if tag == "br":
                message.text_parts.append("\n")
                return
            self._text_depth += 1
            if tag == "a" and values.get("href", "").startswith("http"):
                message.links.append(values["href"])
            return
        if tag == "div" and "tgme_widget_message_text" in classes:
            self._text_depth = 1
        elif tag == "a" and "tgme_widget_message_photo_wrap" in classes:
            if found := BACKGROUND_URL.search(values.get("style", "")):
                message.photos.append(found.group(1))
        elif tag == "video" and values.get("src"):
            message.videos.append(values["src"])
        elif tag == "time" and values.get("datetime") and message.time is None:
            with contextlib.suppress(ValueError):
                message.time = datetime.fromisoformat(values["datetime"])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tags like <br/> open nothing: don't let their implied end tag close a block.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if self._skip_depth:
            self._skip_depth -= 1
        elif self._text_depth:
            self._text_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._text_depth and not self._skip_depth:
            self._current.text_parts.append(re.sub(r"\s+", " ", data))


def parse_channel_page(html: str) -> list[TelegramMessage]:
    parser = _PageParser()
    parser.feed(html)
    return [m for m in parser.messages if m.time is not None]


def message_event(message: TelegramMessage) -> Event:
    assert message.time is not None
    own_channel = f"https://t.me/{message.channel}"
    links = [link for link in message.links if not link.lower().startswith(own_channel)]
    media = [Media(kind=MediaKind.IMAGE, ref=url) for url in message.photos[:4]]
    media += [Media(kind=MediaKind.VIDEO, ref=url) for url in message.videos[:1]]
    text = message.text[:3000]
    return Event(
        id=f"https://t.me/{message.post}",
        platform=Platform.TELEGRAM,
        kind=EventKind.POST,
        author=f"telegram:{message.channel}",
        time_us=int(message.time.timestamp() * 1_000_000),
        created_at=message.time.isoformat(),
        text=text,
        links=tuple(dict.fromkeys(links))[:10],
        hashtags=tuple(dict.fromkeys(t.lower() for t in HASHTAG.findall(text))),
        media=tuple(media),
    )


def pravda_source_channels(path: Path, top: int) -> list[str]:
    """The Telegram channels the Pravda sites republish most (CheckFirst data, see fetch.py)."""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda r: -int(r.get("pravda_articles") or 0))
    return [r["channel"] for r in rows[:top]]


class TelegramSource:
    name = "telegram"

    def __init__(
        self,
        channels: Sequence[str],
        *,
        poll_seconds: float = 300.0,
        client: httpx.AsyncClient | None = None,
        clock: object = time.time,
    ) -> None:
        if not channels:
            raise ValueError("no Telegram channels configured")
        self._channels = [c.lower() for c in channels]
        self._poll_seconds = poll_seconds
        self._client = client or httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT})
        self._clock = clock
        self._newest: dict[str, int] = {}  # channel -> newest message number emitted
        self.polls = 0
        self.errors = 0
        self.watched = len(self._channels)

    def _now_us(self) -> int:
        return int(self._clock() * 1_000_000)  # type: ignore[operator]

    async def fetch(self, channel: str) -> list[TelegramMessage]:
        response = await self._client.get(f"https://t.me/s/{channel}")
        response.raise_for_status()
        return parse_channel_page(response.text)

    async def items(self, cursor: int | None) -> AsyncIterator[SourceItem]:
        """`cursor` is the time of the last completed poll round; messages published before it
        (minus a 10-minute overlap) are not emitted again after a restart."""
        floor_us = (cursor - 600_000_000) if cursor is not None else None
        while True:
            round_us = self._now_us()
            for channel in self._channels:
                try:
                    messages = await self.fetch(channel)
                    self.polls += 1
                except httpx.HTTPError as error:
                    self.errors += 1
                    log.warning(
                        "telegram fetch failed", extra={"channel": channel, "error": str(error)[:200]}
                    )
                    continue
                newest = self._newest.get(channel, 0)
                for message in sorted(messages, key=lambda m: m.number):
                    event = message_event(message)
                    too_old = round_us - event.time_us > MAX_AGE_US
                    already = message.number <= newest or (floor_us is not None and event.time_us < floor_us)
                    if not too_old and not already:
                        yield SourceItem(cursor=round_us, event=event)
                    self._newest[channel] = max(self._newest.get(channel, 0), message.number)
                await asyncio.sleep(0.5)  # spread requests a little
            yield SourceItem(cursor=round_us, event=None)  # marks the round as done
            floor_us = None
            await asyncio.sleep(max(1.0, self._poll_seconds - (self._now_us() - round_us) / 1e6))
