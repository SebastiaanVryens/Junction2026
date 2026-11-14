"""Pictures for the front page, only ones that are already public.

Pictures from individual accounts' posts stay reviewer-only (privacy). A story's picture is, in
order: a public Telegram channel photo or video from the story's evidence; else the picture one of
the story's own sources published: a public Telegram channel post, or a news site's article image
(og:image). It always carries a credit line and a link to where it was published. The browser
loads it from the source's servers; nothing is copied to ours.
"""

from __future__ import annotations

import re
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

TELEGRAM_POST = re.compile(r"^https://(?:www\.)?t\.me/(?:s/)?([a-z0-9_]{4,64})/(\d+)", re.I)
MAX_SOURCES = 4


class PageReader(Protocol):
    def fetch_page(self, url: str) -> Any: ...


def _credit(post: str) -> str:
    if match := TELEGRAM_POST.match(post):
        return f"@{match.group(1)} on Telegram"
    return (urlsplit(post).hostname or "").removeprefix("www.")


def from_evidence(evidence: dict[str, Any]) -> dict[str, str] | None:
    """A public video or photo already in the story's evidence (Telegram channels). A video wins,
    with the first photo as its cover."""
    found: dict[str, dict[str, str]] = {}
    for source in (evidence.get("media") or {}).values():
        if not isinstance(source, dict) or source.get("public") is not True:
            continue
        ref, kind = str(source.get("ref") or ""), str(source.get("kind") or "")
        if ref.startswith("https://") and kind in ("image", "video") and kind not in found:
            post = str(source.get("post") or "")
            found[kind] = {"kind": kind, "src": ref, "credit": _credit(post), "link": post}
    if "video" in found:
        return found["video"] | {"poster": found["image"]["src"] if "image" in found else ""}
    return found.get("image")


def fresh_urls(evidence: dict[str, Any]) -> list[str]:
    """Where to read the story's media fresh: the public Telegram posts its pictures and videos came
    from, then its own sources. (Stored media links expire; the posts do not.)"""
    posts = [
        str(source.get("post") or "")
        for source in (evidence.get("media") or {}).values()
        if isinstance(source, dict) and source.get("public") is True
    ]
    telegram = [p for p in dict.fromkeys(posts) if TELEGRAM_POST.match(p)]
    return [*telegram, *(u for u in source_urls(evidence) if u not in telegram)]


def source_urls(evidence: dict[str, Any]) -> list[str]:
    """The story's own sources (state media, Telegram channels), in their evidence order."""
    headlines = evidence.get("headlines") or {}

    def number(ref: str) -> int:
        digits = "".join(c for c in ref if c.isdigit())
        return int(digits) if digits else 0

    urls = [str((headlines[ref] or {}).get("url") or "") for ref in sorted(headlines, key=number)]
    return [u for u in urls if u.startswith("https://")]


def from_sources(urls: list[str], pages: PageReader, http: httpx.Client) -> dict[str, str] | None:
    """The first picture a source published: a Telegram post's photo, or an article's og:image."""
    from factslens.enrich.lists import KnownLists
    from factslens.ingest.telegram import parse_channel_page
    from factslens.investigator import lens

    for url in urls[:MAX_SOURCES]:
        try:
            if lens.MASTODON_POST.match(url):  # e.g. RT's mirror on a Mastodon server; checked to be public
                target = lens.read_link(url, pages, KnownLists(), http)  # type: ignore[arg-type]
                if target.images:
                    return {"kind": "image", "src": target.images[0], "credit": _credit(url), "link": url}
                continue
            if match := TELEGRAM_POST.match(url):
                channel, number = match.group(1), int(match.group(2))
                response = http.get(f"https://t.me/s/{channel}", params={"before": number + 1})
                if response.status_code != 200:
                    continue
                message = next((m for m in parse_channel_page(response.text) if m.number == number), None)
                photo = next(
                    (p for p in (message.photos if message else []) if p.startswith("https://")), None
                )
                video = next(
                    (v for v in (message.videos if message else []) if v.startswith("https://")), None
                )
                if video:
                    return {"kind": "video", "src": video, "poster": photo or "",
                            "credit": f"@{channel} on Telegram", "link": url}  # fmt: skip
                if photo:
                    return {"kind": "image", "src": photo, "credit": f"@{channel} on Telegram", "link": url}
            else:
                page = pages.fetch_page(url)
                image = str(getattr(page, "image", "") or "")
                if image.startswith("https://"):
                    return {"kind": "image", "src": image, "credit": _credit(url), "link": url}
        except Exception:  # one unreadable source must not cost the front page its picture
            continue
    return None
