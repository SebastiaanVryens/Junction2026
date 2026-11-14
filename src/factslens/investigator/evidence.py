"""Evidence the investigator can gather: linked web pages, domain registration, images, video.

Everything here touches the open internet on behalf of untrusted posts, so:
- page fetches only go to public addresses (no localhost, private ranges or cloud metadata),
  checked again on every redirect, with size and time limits;
- fetched text is data for the model, never instructions (the prompt says so too);
- results are cached in memory so the same page or domain is not fetched twice.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import re
import socket
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "FactsLens/0.1 (research; Junction 2026; +https://srv2159312.hstgr.cloud)"
MAX_PAGE_BYTES = 1_000_000
MAX_PAGE_TEXT = 4000
MAX_REDIRECTS = 3
MAX_IMAGE_BYTES = 600_000
VIDEO_SECONDS = 20
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

Resolver = Callable[[str], list[str]]


class FetchRefused(RuntimeError):
    """The URL is not something we fetch (scheme, port, private address, size, type)."""


def system_resolver(host: str) -> list[str]:
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None)})


def is_public(host: str, resolver: Resolver = system_resolver) -> bool:
    try:
        addresses = resolver(host)
    except OSError:
        return False
    return bool(addresses) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses)


class _TextExtractor(HTMLParser):
    SKIP: frozenset[str] = frozenset({"script", "style", "noscript", "svg", "head", "nav", "footer", "form"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.parts: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            values = dict(attrs)
            if (values.get("name") or values.get("property") or "").lower() in (
                "description",
                "og:description",
            ):
                self.description = self.description or (values.get("content") or "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


def extract_text(html: str) -> tuple[str, str, str]:
    """(title, description, visible text) of an HTML page."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # broken HTML: keep what was parsed
        log.debug("html parse stopped early")
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    return " ".join(parser.title.split())[:300], parser.description[:500], text[:MAX_PAGE_TEXT]


@dataclass(frozen=True)
class Page:
    url: str
    final_url: str
    status: int
    title: str
    description: str
    text: str


@dataclass(frozen=True)
class NewsArticle:
    title: str
    outlet: str
    outlet_site: str
    published: datetime | None
    link: str


# Google News edition per language: (hl, gl, ceid). Each one checked on 14 Nov 2026 to answer
# with results directly; Finnish and Estonian need the region in hl, and Danish has no edition
# (Google redirects it to Norway's). The list covers the languages the Pravda network targets
# around us (its sites for Finland, the Baltics, Sweden, Central Europe), not only big markets.
# Note: the Russian edition mostly returns Russian-language Ukrainian outlets.
NEWS_LANGUAGES = {
    "en": ("en-US", "US", "US:en"), "fi": ("fi-FI", "FI", "FI:fi"), "sv": ("sv", "SE", "SE:sv"),
    "et": ("et-EE", "EE", "EE:et"), "lv": ("lv", "LV", "LV:lv"), "lt": ("lt", "LT", "LT:lt"),
    "ru": ("ru", "RU", "RU:ru"), "uk": ("uk", "UA", "UA:uk"), "pl": ("pl", "PL", "PL:pl"),
    "cs": ("cs", "CZ", "CZ:cs"), "sk": ("sk", "SK", "SK:sk"), "hu": ("hu", "HU", "HU:hu"),
    "ro": ("ro", "RO", "RO:ro"), "bg": ("bg", "BG", "BG:bg"), "sr": ("sr", "RS", "RS:sr"),
    "el": ("el", "GR", "GR:el"), "de": ("de", "DE", "DE:de"), "fr": ("fr", "FR", "FR:fr"),
    "nl": ("nl", "NL", "NL:nl"), "it": ("it", "IT", "IT:it"), "es": ("es-419", "US", "US:es-419"),
    "pt": ("pt-BR", "BR", "BR:pt-419"), "no": ("no", "NO", "NO:no"), "ja": ("ja", "JP", "JP:ja"),
}  # fmt: skip


def parse_news_feed(xml_text: str, limit: int = 8) -> list[NewsArticle]:
    """Items of a Google News RSS search feed: title, outlet, outlet site, date, link."""
    root = ET.fromstring(xml_text)
    articles = []
    for item in root.findall("./channel/item")[:limit]:
        source = item.find("source")
        outlet = (source.text or "").strip() if source is not None else ""
        title = (item.findtext("title") or "").strip()
        if outlet and title.endswith(f" - {outlet}"):
            title = title[: -len(outlet) - 3]
        published = None
        if date := item.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(date)
            except (TypeError, ValueError):
                published = None
        articles.append(
            NewsArticle(
                title=title[:300],
                outlet=outlet[:120],
                outlet_site=(source.get("url", "") if source is not None else "")[:200],
                published=published,
                link=(item.findtext("link") or "")[:500],
            )
        )
    return articles


@dataclass(frozen=True)
class DomainInfo:
    domain: str
    registered: datetime | None
    registrar: str | None

    def age_days(self, now: datetime | None = None) -> int | None:
        if self.registered is None:
            return None
        return ((now or datetime.now(UTC)) - self.registered).days


def _registrar(rdap: dict[str, Any]) -> str | None:
    for entity in rdap.get("entities", []):
        if "registrar" in entity.get("roles", []):
            vcard = entity.get("vcardArray") or [None, []]
            for entry in vcard[1] if len(vcard) > 1 else []:
                if entry and entry[0] == "fn":
                    return str(entry[3])[:200]
    return None


class EvidenceGatherer:
    def __init__(
        self,
        client: httpx.Client | None = None,
        resolver: Resolver = system_resolver,
        rdap_base: str = "https://rdap.org/domain/",
    ) -> None:
        self._http = client or httpx.Client(timeout=12, headers={"User-Agent": USER_AGENT})
        self._resolver = resolver
        self._rdap_base = rdap_base
        self._pages: dict[str, Page | str] = {}
        self._domains: dict[str, DomainInfo | None] = {}
        self._news: dict[tuple[str, str], list[NewsArticle]] = {}
        self._last_news_call = 0.0
        self.news_interval = 2.0  # seconds between news searches

    # --- web pages -------------------------------------------------------------------------

    def fetch_page(self, url: str) -> Page:
        """Fetch a page politely and safely. Raises FetchRefused or httpx errors."""
        cached = self._pages.get(url)
        if isinstance(cached, Page):
            return cached
        if isinstance(cached, str):
            raise FetchRefused(cached)
        try:
            page = self._fetch_page(url)
        except FetchRefused as refusal:
            self._pages[url] = str(refusal)
            raise
        self._pages[url] = page
        return page

    def _check(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise FetchRefused(f"scheme {parts.scheme!r} not allowed")
        if parts.port not in (None, 80, 443):
            raise FetchRefused(f"port {parts.port} not allowed")
        if not parts.hostname or not is_public(parts.hostname, self._resolver):
            raise FetchRefused(f"{parts.hostname} is not a public internet address")

    def _fetch_page(self, url: str) -> Page:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            self._check(current)
            with self._http.stream("GET", current, follow_redirects=False) as response:
                if response.is_redirect and "location" in response.headers:
                    current = urljoin(current, response.headers["location"])
                    continue
                kind = response.headers.get("content-type", "").lower()
                if "html" not in kind and "text/plain" not in kind:
                    raise FetchRefused(f"content type {kind or 'unknown'} is not a web page")
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_PAGE_BYTES:
                        break
                html = body[:MAX_PAGE_BYTES].decode(response.encoding or "utf-8", errors="replace")
                title, description, text = (
                    extract_text(html) if "html" in kind else ("", "", html[:MAX_PAGE_TEXT])
                )
                return Page(url, current, response.status_code, title, description, text)
        raise FetchRefused("too many redirects")

    # --- domains ---------------------------------------------------------------------------

    def domain_info(self, host: str) -> DomainInfo | None:
        """Registration date and registrar via RDAP. Tries the host, then its parent domains
        (france.news-pravda.com -> news-pravda.com), because only registered domains have records."""
        host = host.lower().removeprefix("www.").rstrip(".")
        if not DOMAIN_RE.match(host):
            return None
        labels = host.split(".")
        for start in range(0, max(len(labels) - 1, 1)):
            candidate = ".".join(labels[start:])
            if candidate in self._domains:
                return self._domains[candidate]
            info = self._rdap(candidate)
            if info is not None:
                for i in range(start + 1):
                    self._domains[".".join(labels[i:])] = info
                return info
        self._domains[host] = None
        return None

    def _rdap(self, domain: str) -> DomainInfo | None:
        try:
            response = self._http.get(self._rdap_base + domain, follow_redirects=True)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        data = response.json()
        registered = next(
            (e.get("eventDate") for e in data.get("events", []) if e.get("eventAction") == "registration"),
            None,
        )
        when = None
        if isinstance(registered, str):
            try:
                when = datetime.fromisoformat(registered.replace("Z", "+00:00"))
            except ValueError:
                when = None
        return DomainInfo(domain, when, _registrar(data))

    # --- news ------------------------------------------------------------------------------

    def search_news(self, query: str, language: str = "en", days: int = 7) -> list[NewsArticle] | None:
        """Independent news coverage of a claim (Google News search feed, no key needed). At most
        one request every two seconds; results cached per query. None means the search itself failed,
        which is not the same as finding no coverage."""
        language = language if language in NEWS_LANGUAGES else "en"
        key = (query.strip().lower(), language)
        if key in self._news:
            return self._news[key]
        hl, gl, ceid = NEWS_LANGUAGES[language]
        wait = self.news_interval - (time.monotonic() - self._last_news_call)
        if wait > 0:
            time.sleep(wait)
        self._last_news_call = time.monotonic()
        try:
            response = self._http.get(
                "https://news.google.com/rss/search",
                params={"q": f"{query} when:{days}d", "hl": hl, "gl": gl, "ceid": ceid},
            )
            response.raise_for_status()
            articles = parse_news_feed(response.text)
        except (httpx.HTTPError, ET.ParseError) as error:
            log.warning("news search failed", extra={"query": query[:100], "error": str(error)[:200]})
            return None
        self._news[key] = articles
        return articles

    # --- media -----------------------------------------------------------------------------

    def image_from_url(self, url: str) -> str | None:
        """An image from another platform (e.g. Telegram's CDN), fetched only from public addresses."""
        try:
            self._check(url)
            response = self._http.get(url, follow_redirects=False)
        except (FetchRefused, httpx.HTTPError):
            return None
        kind = response.headers.get("content-type", "")
        if (
            response.status_code != 200
            or not kind.startswith("image/")
            or len(response.content) > MAX_IMAGE_BYTES
        ):
            return None
        return f"data:{kind.split(';')[0]};base64," + base64.b64encode(response.content).decode()

    def image_data_url(self, did: str, cid: str) -> str | None:
        """A Bluesky image as a small JPEG data URL (the CDN's thumbnail size)."""
        url = f"https://cdn.bsky.app/img/feed_thumbnail/plain/{did}/{cid}@jpeg"
        try:
            response = self._http.get(url, follow_redirects=True)
        except httpx.HTTPError:
            return None
        if response.status_code != 200 or len(response.content) > MAX_IMAGE_BYTES:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(response.content).decode()

    def video_data_url(self, did: str, cid: str) -> str | None:
        """The first seconds of a Bluesky video, re-encoded small (2 fps, 320 px wide, no audio)."""
        try:
            import imageio_ffmpeg
        except ImportError:
            return None
        playlist = f"https://video.bsky.app/watch/{did}/{cid}/playlist.m3u8"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "clip.mp4"
            command = [
                imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-t", str(VIDEO_SECONDS),
                "-i", playlist, "-vf", "fps=2,scale=320:-2", "-an", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "32", str(out),
            ]  # fmt: skip
            started = time.perf_counter()
            try:
                subprocess.run(command, capture_output=True, timeout=90, check=True)
            except (subprocess.SubprocessError, OSError) as error:
                log.info("video not available", extra={"error": str(error)[:200]})
                return None
            if not out.exists() or out.stat().st_size > 3_000_000:
                return None
            log.info(
                "video clip ready",
                extra={"bytes": out.stat().st_size, "seconds": time.perf_counter() - started},
            )
            return "data:video/mp4;base64," + base64.b64encode(out.read_bytes()).decode()
