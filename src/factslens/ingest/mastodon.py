"""Mastodon source: public local timelines of the servers on IFTAS's list (no login, no key).

Why: Bluesky has suspended most accounts of the Portal Kombat network that IFTAS lists (2,508 of
2,850 on 14 Nov 2026), but the servers they came from keep running, and new accounts appear there
(e.g. two-week-old accounts with random-letter names and 1,000 posts each). Watching the source
servers lets the detector see the network where Bluesky no longer does.

One identity per person: an account that is bridged to Bluesky and still active there is already
in the Jetstream feed, so its Mastodon posts are skipped. The detector also drops the second copy
of any post by its original URL (factslens.detector.groups.post_key), as a safety net.

Politeness: one request per server per poll (default every 5 minutes) with a user agent that names
the project; a failing server is retried with growing pauses (up to an hour). Only posts from the
last 90 minutes are emitted.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from factslens.common.models import Event, EventKind, Media, MediaKind, Platform
from factslens.enrich.lists import bridgy_handle
from factslens.ingest.source import SourceItem

log = logging.getLogger(__name__)

USER_AGENT = "FactsLens/0.1 (research on coordinated inauthentic behaviour; Junction 2026)"
APPVIEW_PROFILES = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfiles"
MAX_AGE_US = 90 * 60 * 1_000_000
PAGE = 40  # Mastodon's maximum page size for timelines
BRIDGE_CHECK_SECONDS = 6 * 3600  # re-check whether an account is active on Bluesky this often
MAX_BACKOFF_SECONDS = 3600.0


class _ContentParser(HTMLParser):
    """Visible text and outgoing links of a Mastodon post's HTML (mentions and hashtags excluded)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag == "br":
            self.parts.append("\n")
        elif tag == "p" and self.parts:
            self.parts.append("\n\n")
        elif tag == "a":
            href, classes = values.get("href", ""), values.get("class", "")
            if href.startswith(("http://", "https://")) and not {"mention", "hashtag"} & set(classes.split()):
                self.links.append(href)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    @property
    def text(self) -> str:
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        return re.sub(r" *\n *", "\n", text).strip()


def parse_content(html: str) -> tuple[str, list[str]]:
    parser = _ContentParser()
    parser.feed(html or "")
    return parser.text, parser.links


def author_id(username: str, server: str) -> str:
    return f"mastodon:{username}@{server}".lower()


def status_event(status: dict[str, Any], server: str) -> Event | None:
    """A post from a server's public local timeline as an Event; None for boosts and odd records."""
    account = status.get("account") or {}
    username, url, created = (
        account.get("username"),
        status.get("url") or status.get("uri"),
        status.get("created_at"),
    )
    if status.get("reblog") or not username or not isinstance(url, str) or not isinstance(created, str):
        return None
    try:
        when = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    text, links = parse_content(status.get("content") or "")
    if warning := (status.get("spoiler_text") or "").strip():
        text = f"{warning}\n\n{text}" if text else warning
    card = (status.get("card") or {}).get("url")
    if isinstance(card, str) and card.startswith(("http://", "https://")):
        links.append(card)
    media: list[Media] = []
    for attachment in (status.get("media_attachments") or [])[:4]:
        kind = attachment.get("type")
        ref = attachment.get("preview_url") if kind == "image" else attachment.get("url")
        if isinstance(ref, str) and ref.startswith("https://") and kind in ("image", "video", "gifv"):
            media.append(
                Media(
                    kind=MediaKind.IMAGE if kind == "image" else MediaKind.VIDEO,
                    ref=ref,
                    alt=(attachment.get("description") or None) and str(attachment["description"])[:500],
                )
            )
    language = status.get("language")
    reply = status.get("in_reply_to_id")
    return Event(
        id=url,
        platform=Platform.MASTODON,
        kind=EventKind.POST,
        author=author_id(username, server),
        time_us=int(when.timestamp() * 1_000_000),
        created_at=when.isoformat(),
        text=text[:3000],
        langs=(language,) if isinstance(language, str) and language else (),
        links=tuple(dict.fromkeys(links))[:10],
        hashtags=tuple(dict.fromkeys(str(t.get("name", "")).lower() for t in status.get("tags") or []))[:20],
        mentions=tuple(str(m.get("acct", "")) for m in status.get("mentions") or [])[:20],
        reply_to=f"https://{server}/statuses/{reply}" if reply else None,
        media=tuple(media),
    )


def listed_servers(rows: Iterable[dict[str, str]]) -> list[str]:
    """Servers from IFTAS's instances list (eval/ground_truth/data/instances.csv), most observed first."""
    ordered = sorted(rows, key=lambda r: -int(r.get("iftas_observations") or 0))
    return list(dict.fromkeys(r["instance"].strip().lower() for r in ordered if r.get("instance")))


class MastodonSource:
    name = "mastodon"

    def __init__(
        self,
        servers: Sequence[str],
        *,
        poll_seconds: float = 300.0,
        client: httpx.AsyncClient | None = None,
        clock: Any = time.time,
    ) -> None:
        if not servers:
            raise ValueError("no Mastodon servers configured")
        self._servers = [s.lower() for s in servers]
        self._poll_seconds = poll_seconds
        self._client = client or httpx.AsyncClient(
            timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self._clock = clock
        self._newest: dict[str, int] = {}  # server -> newest status id seen
        self._failures: Counter[str] = Counter()
        self._retry_at: dict[str, float] = {}
        self._on_bluesky: dict[tuple[str, str], tuple[bool, float]] = {}  # (user, server) -> (active, when)
        self.polls = 0
        self.errors = 0
        self.watched = len(self._servers)
        self.skipped_bridged = 0

    async def fetch(self, server: str) -> list[dict[str, Any]]:
        params = {"local": "true", "limit": str(PAGE)}
        if server in self._newest:
            params["min_id"] = str(self._newest[server])
        response = await self._client.get(f"https://{server}/api/v1/timelines/public", params=params)
        response.raise_for_status()
        data = response.json()
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    async def active_on_bluesky(self, accounts: set[tuple[str, str]]) -> set[tuple[str, str]]:
        """Which (username, server) accounts are bridged to Bluesky and still active there."""
        now = self._clock()
        unknown = [
            a for a in accounts if now - self._on_bluesky.get(a, (False, -1e12))[1] > BRIDGE_CHECK_SECONDS
        ]
        for start in range(0, len(unknown), 25):  # getProfiles takes at most 25 actors
            batch = {
                bridgy_handle(server, user): (user, server) for user, server in unknown[start : start + 25]
            }
            try:
                response = await self._client.get(APPVIEW_PROFILES, params=[("actors", h) for h in batch])
                response.raise_for_status()
                found = {str(p.get("handle", "")).lower() for p in response.json().get("profiles", [])}
            except (httpx.HTTPError, ValueError) as error:
                # Unknown: keep the Mastodon posts; the detector still drops duplicate posts.
                log.warning("bluesky bridge check failed", extra={"error": str(error)[:200]})
                continue
            for handle, account in batch.items():
                self._on_bluesky[account] = (handle in found, now)
        return {a for a in accounts if self._on_bluesky.get(a, (False, 0.0))[0]}

    def _now_us(self) -> int:
        return int(self._clock() * 1_000_000)

    async def _poll(self, server: str, round_us: int, floor_us: int | None) -> list[Event]:
        try:
            statuses = await self.fetch(server)
        except (httpx.HTTPError, ValueError) as error:
            self.errors += 1
            self._failures[server] += 1
            pause = min(MAX_BACKOFF_SECONDS, max(60.0, self._poll_seconds) * 2 ** self._failures[server])
            self._retry_at[server] = self._clock() + pause
            log.warning(
                "mastodon fetch failed",
                extra={"server": server, "error": str(error)[:200], "retry_in_s": round(pause)},
            )
            return []
        self.polls += 1
        self._failures.pop(server, None)
        events = []
        for status in statuses:
            if str(status.get("id", "")).isdigit():
                self._newest[server] = max(self._newest.get(server, 0), int(status["id"]))
            event = status_event(status, server)
            if event is None or round_us - event.time_us > MAX_AGE_US:
                continue
            if floor_us is not None and event.time_us < floor_us:
                continue
            events.append(event)
        events.sort(key=lambda e: e.time_us)
        bridged = await self.active_on_bluesky({_account(e) for e in events})
        kept = [e for e in events if _account(e) not in bridged]
        self.skipped_bridged += len(events) - len(kept)
        return kept

    async def items(self, cursor: int | None) -> AsyncIterator[SourceItem]:
        """`cursor` is the time of the last completed poll round; posts published before it (minus
        a 10-minute overlap) are not emitted again after a restart."""
        floor_us = (cursor - 600_000_000) if cursor is not None else None
        while True:
            round_us = self._now_us()
            for server in self._servers:
                if self._retry_at.get(server, 0.0) > self._clock():
                    continue
                for event in await self._poll(server, round_us, floor_us):
                    yield SourceItem(cursor=round_us, event=event)
                await asyncio.sleep(0.2)  # spread requests a little
            yield SourceItem(cursor=round_us, event=None)  # marks the round as done
            floor_us = None
            await asyncio.sleep(max(1.0, self._poll_seconds - (self._now_us() - round_us) / 1e6))


def _account(event: Event) -> tuple[str, str]:
    user, _, server = event.author.removeprefix("mastodon:").partition("@")
    return user, server
