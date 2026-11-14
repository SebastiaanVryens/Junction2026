"""The public web app: pipeline status, candidate clusters and their explainable signals.

    python -m factslens.api --host 0.0.0.0 --port 8000

Privacy: the public sees the shared message, the signals and aggregate account statistics.
Account names and individual posts are shown only in reviewer mode, unlocked with
REVIEWER_TOKEN (CLAUDE.md §7: "show content only when the reviewer needs it").
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from psycopg_pool import AsyncConnectionPool

from factslens import __version__
from factslens.api import graph as provenance
from factslens.api.metrics import review_summary
from factslens.api.repository import TIERS, PostgresRepository, Repository
from factslens.common import db
from factslens.common.settings import MissingSettingError, get_settings
from factslens.enrich.lists import KnownLists, account_server
from factslens.investigator.llm import BudgetExhausted, CallRecord, ChatClient
from factslens.investigator.translate import Chat, translate

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")

# A service is healthy if it reported within this many seconds. Polling sources report only during
# a round (every 5 minutes; a Mastodon round itself takes up to 3), and the investigator only
# between investigations (up to a few minutes each), so they get limits that match.
STALE_AFTER_SECONDS = 60.0
STALE_AFTER_BY_SERVICE = {"telegram": 600.0, "mastodon": 600.0, "investigator": 420.0}
EXPECTED_SERVICES = ("ingest", "telegram", "mastodon", "detector", "enrich", "investigator")
REVIEW_COOKIE = "factslens_review"
ACCOUNT_MARKS = {"real": "real person", "inauthentic": "inauthentic"}
CAMPAIGN_KINDS = {
    "propaganda": "Propaganda / influence operation",
    "bots": "Bot network / fake amplification",
    "spam": "Spam / scam",
    "organic": "Organic",
    "unclear": "Unclear",
}
NETWORK_SERVER_MIN_LISTED = 10  # 'network server': IFTAS lists at least this many accounts there
MAX_LENS_JOBS = 300  # in memory only, like translations
LENS_PER_VISITOR = (6, 600.0)  # checks per visitor per 10 minutes
LENS_PER_HOUR = 60  # all visitors together
LENS_CACHE_SECONDS = 6 * 3600
HOLD_SECONDS = 120  # renewed at every step; released when no one is waiting
ASK_CACHE_SECONDS = 600  # the data moves; the same question is answered again after 10 min
MAX_TRANSLATIONS = 5000  # kept in memory only; never written to the database
WINDOW_HOURS = 24

REVIEW_DECISIONS = {"confirm": "Confirmed", "false_alarm": "False alarm", "unsure": "Unsure"}
# What was confirmed: Hostinger's propaganda, bot-driven amplification and other bad actors.
REVIEW_CATEGORIES = {
    "propaganda": "Propaganda / influence operation",
    "bots": "Bot network / fake amplification",
    "spam": "Spam / scam",
    "other": "Other coordinated inauthentic",
}
VERDICT_LABELS = {"staged": "Staged", "organic": "Organic", "insufficient_evidence": "Insufficient evidence"}

TIER_LABELS = {
    "suspicious": "Suspicious",
    "watch": "Watch",
    "pending": "Pending",
    "declared_automation": "Declared bots",
    "likely_organic": "Likely organic",
}


def build_view(statuses: dict[str, db.ServiceStatus], now: datetime | None = None) -> dict[str, Any]:
    """Everything the status page and /health show, computed in one place."""
    now = now or datetime.now(UTC)
    services: dict[str, dict[str, Any]] = {}
    for name in EXPECTED_SERVICES:
        status = statuses.get(name)
        age = (now - status.updated_at).total_seconds() if status else None
        services[name] = {
            "reported_seconds_ago": round(age, 1) if age is not None else None,
            "healthy": age is not None and age <= STALE_AFTER_BY_SERVICE.get(name, STALE_AFTER_SECONDS),
            "data": status.data if status else {},
        }
    ingest, detector = services["ingest"]["data"], services["detector"]["data"]
    last_event_us = ingest.get("last_time_us")
    per_minute = detector.get("events_last_minute") or {}
    return {
        "healthy": all(s["healthy"] for s in services.values()),
        "services": services,
        "checked_at": now.isoformat(timespec="seconds"),
        "last_event_seconds_ago": round(time.time() - last_event_us / 1_000_000, 1)
        if last_event_us
        else None,
        "events_per_second": round(sum(per_minute.values()) / 60, 1) if per_minute else None,
        "read_per_minute": read_per_minute(services),
        "watched": {name: services[name]["data"].get("watched") for name in ("telegram", "mastodon")},
        "version": __version__,
    }


SOURCES = {"ingest": "Bluesky", "telegram": "Telegram", "mastodon": "Mastodon"}


def read_per_minute(services: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Posts and reposts each source has read per minute since it started (from its own counters)."""
    rates = {}
    for service, platform in SOURCES.items():
        data = services.get(service, {}).get("data") or {}
        total, uptime = data.get("total"), data.get("uptime_seconds")
        if isinstance(total, int | float) and isinstance(uptime, int | float) and uptime > 0:
            rates[platform] = round(total / uptime * 60)
    return rates


def sparkline(values: list[int], width: int = 600, height: int = 80) -> str:
    """SVG polyline points for a per-minute activity chart."""
    if not values:
        return ""
    top = max(max(values), 1)
    step = width / max(len(values) - 1, 1)
    return " ".join(f"{i * step:.1f},{height - v / top * (height - 4) - 2:.1f}" for i, v in enumerate(values))


def account_age_days(created: datetime | None, now: datetime) -> int | None:
    return (now - created).days if created else None


def fired_names(signals: dict[str, Any] | None) -> list[str]:
    return [s["name"] for s in (signals or {}).get("signals", []) if s.get("fired")]


def post_url(post_id: str, handle: str | None) -> str | None:
    if post_id.startswith("https://"):  # Telegram and other web sources: the id is the URL
        return post_id
    """at://did/app.bsky.feed.post/rkey -> https://bsky.app/profile/<handle or did>/post/<rkey>"""
    parts = post_id.removeprefix("at://").split("/")
    if len(parts) != 3:
        return None
    return f"https://bsky.app/profile/{handle or parts[0]}/post/{parts[2]}"


def media_view(kind: str, ref: str, did: str | None) -> dict[str, str | None] | None:
    """Where the browser loads a post's image or video still from: the platform's own servers
    (Bluesky CDN by author DID and blob CID; Telegram and Mastodon give full URLs). Nothing is
    copied to our server."""
    if ref.startswith("https://"):
        return {"kind": kind, "thumb": ref if kind == "image" else None, "full": ref}
    if not did:
        return None
    if kind == "image":
        return {
            "kind": kind,
            "thumb": f"https://cdn.bsky.app/img/feed_thumbnail/plain/{did}/{ref}@jpeg",
            "full": f"https://cdn.bsky.app/img/feed_fullsize/plain/{did}/{ref}@jpeg",
        }
    if kind == "video":
        return {
            "kind": kind,
            "thumb": f"https://video.bsky.app/watch/{did}/{ref}/thumbnail.jpg",
            "full": None,
        }
    return None


log = logging.getLogger("factslens.api")


STORY_STATUS = {
    "contested": "Contested: versions contradict",
    "consistent": "Consistent",
    "only_state_media": "Only state media report it",
    "insufficient_coverage": "Too little coverage to compare",
}


def story_links(rows: list[dict[str, Any]], cluster_id: str) -> list[dict[str, Any]]:
    """For a cluster page: the stories this cluster appears in, and which version it pushes."""
    links = []
    for row in rows:
        ref = next((r for r, cid in (row.get("refs") or {}).items() if cid == cluster_id), None)
        version = next(
            (
                a.get("version")
                for a in row.get("amplification") or []
                if a.get("ref") == ref and a.get("pushes", "unrelated") != "unrelated"
            ),
            None,
        )
        if version is None:  # matched by keyword only: not linked from the cluster page
            continue
        links.append(
            {"id": row["id"], "title": row["title"], "status": row.get("story_status"), "version": version}
        )
    return links


ASK_EXAMPLES = [
    "What are the biggest coordinated campaigns right now, and what do they push?",
    "Is anyone pushing stories about Finland or NATO?",
    "Which Mastodon servers and Telegram channels feed the most suspicious groups?",
    "Which news stories are contested today, and who contradicts whom?",
    "Which websites do suspicious groups link to, and are any on propaganda lists?",
    "Are there spam or scam networks active on Bluesky?",
    "Is the war between Ukraine and Russia ending? What do independent outlets say, and what is pushed?",
]


def cite(text: str, refs: dict[str, dict[str, Any]]) -> Markup:
    """The answer with its [G2, K1] citations as links (escaped first, so no HTML gets through)."""

    def link(match: re.Match[str]) -> str:
        parts = []
        for ref in re.findall(r"[GKSDON]\d{1,3}", match.group(0)):
            item = refs.get(ref)
            if item is None:
                continue
            label = escape(item.get("label") or ref)
            if item.get("url"):
                outside = (
                    ' target="_blank" rel="noopener noreferrer"' if item["url"].startswith("http") else ""
                )
                parts.append(
                    f'<a class="chip" href="{escape(item["url"])}" title="{label}"{outside}>{ref}</a>'
                )
            else:
                parts.append(f'<span class="chip" title="{label}">{ref}</span>')
        if len(parts) > 4:  # a long run of chips is noise in a sentence; the evidence list has them all
            parts = [*parts[:3], f'<span class="chip">+{len(parts) - 3} more below</span>']
        return " ".join(parts)

    return Markup(re.sub(r"\[[GKSDON]\d{1,3}(?:\s*,\s*[GKSDON]\d{1,3})*\]", link, str(escape(text))))


LENS_LABELS = {
    "no_concerns_found": "No concerns found",
    "be_careful": "Be careful",
    "likely_coordinated_or_misleading": "Likely coordinated or misleading",
    "insufficient_evidence": "Not enough evidence to say",
}


def live_numbers(raw: dict[str, Any], lists: KnownLists) -> dict[str, Any]:
    """The pitch numbers, each with its unit: flagged = in a suspicious or watch cluster."""
    # Only servers where IFTAS documented many network accounts: big general servers (mastodon.social,
    # with one or two listed accounts) would count thousands of ordinary users.
    per_server: dict[str, int] = {}
    for handle in raw.get("unlisted_handles") or []:
        server = account_server(handle)
        if server and lists.server_accounts.get(server, 0) >= NETWORK_SERVER_MIN_LISTED:
            per_server[server] = per_server.get(server, 0) + 1
    servers = sorted(per_server.items(), key=lambda kv: -kv[1])
    listed_domains = sorted(set(lists.domain_hits(raw.get("domains") or [])))
    return {
        key: raw.get(key) or 0
        for key in (
            "flagged_clusters",
            "flagged_accounts",
            "listed_accounts",
            "removed_accounts",
            "telegram_first_clusters",
            "investigated",
            "staged",
            "campaigns",
            "staged_campaigns",
            "recycled_pictures",
            "recycled_mismatch",
        )
    } | {
        "since": raw.get("since"),
        "unlisted_on_listed_servers": sum(per_server.values()),
        "listed_servers_involved": servers,
        "listed_domains": listed_domains,
        "lists_loaded": lists.loaded,
    }


def create_app(
    repository: Repository | None = None,
    reviewer_token: str | None = None,
    translator: Chat | None = None,
    lists: KnownLists | None = None,
) -> FastAPI:
    """`repository`, `reviewer_token` and `translator` are injected in tests; in production they come
    from settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.translations = OrderedDict()
        app.state.lens_jobs = OrderedDict()
        app.state.ask_jobs = OrderedDict()
        app.state.lens_visits = {}
        if repository is not None:
            app.state.lists = lists or KnownLists()
            app.state.repo = repository
            app.state.reviewer_token = reviewer_token
            app.state.translator = translator
            yield
            return
        settings = get_settings()
        app.state.lists = KnownLists.load(settings.resolved(settings.ground_truth_dir))
        token = settings.reviewer_token.get_secret_value() if settings.reviewer_token else None
        pool = AsyncConnectionPool(
            db.database_url(settings), min_size=1, max_size=4, open=False, kwargs={"autocommit": True}
        )
        await pool.open()
        app.state.repo = PostgresRepository(pool)
        app.state.reviewer_token = token
        loop = asyncio.get_running_loop()

        def log_call(record: CallRecord) -> None:  # runs in the translation's worker thread
            asyncio.run_coroutine_threadsafe(app.state.repo.log_model_call(record.__dict__), loop)

        try:
            # people are waiting: retry for 90 s, enough for a background call in flight to end
            app.state.translator = ChatClient.from_settings(settings, logger=log_call, max_attempts=7)
        except MissingSettingError:
            app.state.translator = None
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="FactsLens", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)

    def repo(request: Request) -> Repository:
        return request.app.state.repo  # type: ignore[no-any-return]

    def cookie_value(token: str) -> str:
        return hashlib.sha256(f"factslens-review:{token}".encode()).hexdigest()

    def is_reviewer(request: Request) -> bool:
        token = request.app.state.reviewer_token
        cookie = request.cookies.get(REVIEW_COOKIE)
        return bool(token and cookie and hmac.compare_digest(cookie, cookie_value(token)))

    def page(request: Request, template: str, context: dict[str, Any]) -> HTMLResponse:
        base = {"reviewer": is_reviewer(request), "tier_labels": TIER_LABELS, "version": __version__}
        return TEMPLATES.TemplateResponse(request, template, base | context)

    async def view(request: Request) -> dict[str, Any]:
        try:
            data = build_view(await repo(request).statuses())
        except Exception as error:  # the page must render even when the database is down
            return build_view({}) | {
                "error": f"status store unavailable: {type(error).__name__}",
                "tiers": {},
            }
        try:
            data["tiers"] = await repo(request).tier_counts(WINDOW_HOURS)
        except Exception:
            data["tiers"] = {}
        return data

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        data = await view(request)
        summary = {
            "healthy": data["healthy"],
            "services": {
                name: {"healthy": s["healthy"], "reported_seconds_ago": s["reported_seconds_ago"]}
                for name, s in data["services"].items()
            },
            "last_event_seconds_ago": data["last_event_seconds_ago"],
            "version": data["version"],
        }
        return JSONResponse(summary, status_code=200 if data["healthy"] else 503)

    @app.get("/", response_class=HTMLResponse)
    async def status_page(request: Request) -> HTMLResponse:
        return page(request, "status.html", await view(request))

    @app.get("/status/fragment", response_class=HTMLResponse)
    async def status_fragment(request: Request) -> HTMLResponse:
        return page(request, "_status.html", await view(request))

    @app.get("/clusters", response_class=HTMLResponse)
    async def cluster_list(
        request: Request, tier: str | None = None, reviewed: str | None = None
    ) -> HTMLResponse:
        if (tier is not None and tier not in TIERS) or reviewed not in (None, "no"):
            raise HTTPException(404)
        unreviewed = reviewed == "no"
        rows = await repo(request).clusters(tier, WINDOW_HOURS, 200, unreviewed=unreviewed)
        for row in rows:
            row["fired"] = fired_names(row.get("signals"))
        counts = await repo(request).tier_counts(WINDOW_HOURS)
        return page(
            request,
            "clusters.html",
            {
                "clusters": rows,
                "tier": tier,
                "tiers": TIERS,
                "counts": counts,
                "unreviewed": unreviewed,
                "reviewed_count": await repo(request).reviewed_count(WINDOW_HOURS),
                "total": sum(counts.values()),
            },
        )

    @app.get("/clusters/{cluster_id}", response_class=HTMLResponse)
    async def cluster_page(request: Request, cluster_id: str) -> HTMLResponse:
        cluster = await repo(request).cluster(cluster_id)
        if cluster is None:
            raise HTTPException(404)
        now = datetime.now(UTC)
        members = await repo(request).members(cluster_id)
        for member in members:
            member["age_days"] = account_age_days(member.get("created_at"), now)
        reviewer = is_reviewer(request)
        # Names and posts are public for already-public cases (team decision, 14 Nov): accounts on
        # IFTAS's public list and accounts Bluesky has removed. Reviewers see everyone.
        for member in members:
            member["public_case"] = bool(member.get("on_public_list")) or member.get("status") in (
                "unavailable",
                "external",  # public Telegram channels
            )
        marks = await repo(request).account_marks(
            [m["account_hash"] for m in members if m.get("account_hash")]
        )
        for member in members:
            member["mark"] = marks.get(member.get("account_hash") or "")
        posts = [
            p
            for p in await repo(request).posts(cluster_id)
            if reviewer or p.get("on_public_list") or p.get("status") in ("unavailable", "external")
        ]
        translations: OrderedDict[str, tuple[str, str]] = request.app.state.translations
        visible_media: dict[str, dict[str, str | None]] = {}  # media ref -> view, from posts shown here
        for post in posts:
            post["english"] = translations.get(f"post:{post['post_id']}")
            post["url"] = post_url(post["post_id"], post.get("handle"))
            did = (
                post["post_id"].removeprefix("at://").split("/")[0]
                if post["post_id"].startswith("at://")
                else None
            )
            post["media"] = []
            for m in post.get("media_refs") or []:
                if m.get("kind") in ("image", "video") and (view := media_view(m["kind"], m["ref"], did)):
                    view["post"] = post["url"]
                    post["media"].append(view)
                    visible_media[m["ref"]] = view
        signals = cluster.get("signals") or {}
        ages = sorted(a for m in members if (a := m["age_days"]) is not None)
        investigation = await repo(request).investigation(cluster_id)
        evidence = (investigation or {}).get("evidence") or {}
        sources: dict[str, str] = {}  # evidence id -> link, for the report's references
        for ref, url in (evidence.get("pages") or {}).items():
            sources[ref] = url
        # Media the model saw, shown only when its post is visible to this viewer (reviewer, or a
        # public case): the same rule as for post texts.
        media: dict[str, dict[str, str | None]] = {}
        for ref, value in {**(evidence.get("images") or {}), **(evidence.get("video") or {})}.items():
            if isinstance(value, str) and (view := visible_media.get(value)):
                media[ref] = view
                if link := view["full"] or view.get("post"):
                    sources[ref] = link
        for ref, article in (evidence.get("news") or {}).items():
            if str(article.get("link", "")).startswith("https://"):
                sources[ref] = article["link"]
        if reviewer:
            for ref, post_id in (evidence.get("posts") or {}).items():
                if url := post_url(post_id, None):
                    sources[ref] = url
        return page(
            request,
            "cluster.html",
            {
                "c": cluster,
                "tier": cluster["tier_or_pending"],
                "signals": signals.get("signals", []),
                "reasons": signals.get("reasons", []),
                "coverage": signals.get("profile_coverage"),
                "members": members,
                "posts": posts,
                "related": await repo(request).related(cluster_id),
                "spark": sparkline(list(cluster.get("per_minute") or [])),
                "median_age": ages[len(ages) // 2] if ages else None,
                "removed": sum(m.get("status") == "unavailable" for m in members),
                "investigation": investigation,
                "media": media,
                "report": (investigation or {}).get("report"),
                "grounding": evidence.get("grounding"),
                "sources": sources,
                "verdict_labels": VERDICT_LABELS,
                "reviews": await repo(request).reviews(cluster_id),
                "categories": REVIEW_CATEGORIES,
                "account_marks": ACCOUNT_MARKS,
                "campaign": await repo(request).campaign_for_cluster(cluster_id),
                "stories": story_links(await repo(request).stories_for_cluster(cluster_id), cluster_id),
                "recycled": await repo(request).recycled_for_cluster(cluster_id),
                "saved": request.query_params.get("saved"),
                "sample_mode": request.query_params.get("mode") == "sample",
                "label_english": translations.get(f"label:{cluster_id}") if reviewer or posts else None,
                "can_translate": reviewer and request.app.state.translator is not None,
                "translate_failed": request.query_params.get("translate") == "failed",
                "decisions": REVIEW_DECISIONS,
            },
        )

    @app.post("/clusters/{cluster_id}/review", response_model=None)
    async def review_cluster(request: Request, cluster_id: str) -> Response:
        if not is_reviewer(request):
            raise HTTPException(403, "reviewer mode required")
        form = parse_qs((await request.body()).decode(errors="replace"))
        # "choice" comes from the review bar ("confirm:spam", "false_alarm", "skip");
        # "decision" (+ optional "category") is the plain form.
        decision, _, category = form.get("choice", form.get("decision", [""]))[0].partition(":")
        category = category or form.get("category", [""])[0]
        sample = form.get("mode", [""])[0] == "sample"
        if decision == "skip":
            return await next_cluster_redirect(request, cluster_id, saved=None, sample=sample)
        if decision not in REVIEW_DECISIONS or (category and category not in REVIEW_CATEGORIES):
            raise HTTPException(400, "unknown decision")
        cluster = await repo(request).cluster(cluster_id)
        if cluster is None:
            raise HTTPException(404)
        investigation = await repo(request).investigation(cluster_id)
        note = form.get("note", [""])[0].strip()[:1000] or None
        await repo(request).add_review(
            cluster_id,
            decision,
            note,
            cluster.get("tier"),
            (investigation or {}).get("verdict"),
            category if decision == "confirm" and category else None,
            sampled=sample,
        )
        return await next_cluster_redirect(request, cluster_id, saved=cluster_id, sample=sample)

    @app.get("/review/sample", response_model=None)
    async def review_sample(request: Request) -> Response:
        """Start reviewing a random sample: clusters from every tier, each equally likely."""
        if not is_reviewer(request):
            return RedirectResponse("/review", status_code=303)
        return await next_cluster_redirect(request, None, saved=None, sample=True)

    # --- the Lens: paste any link -------------------------------------------------------------

    def visitor(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")

    def allowed(request: Request) -> bool:
        now = time.time()
        visits: dict[str, list[float]] = request.app.state.lens_visits
        limit, window = LENS_PER_VISITOR
        mine = [t for t in visits.get(visitor(request), []) if now - t < window]
        everyone = sum(1 for times in visits.values() for t in times if now - t < 3600)
        if len(mine) >= limit or everyone >= LENS_PER_HOUR:
            return False
        visits[visitor(request)] = [*mine, now]
        return True

    async def hold(app_: FastAPI) -> None:
        """Background Nemotron work waits while someone waits for an answer (see migration 012)."""
        try:
            await app_.state.repo.hold_nemotron(HOLD_SECONDS)
        except Exception as error:  # never fail an answer over this
            log.warning("could not hold Nemotron", extra={"error": str(error)[:200]})

    async def release(app_: FastAPI) -> None:
        jobs = [*app_.state.lens_jobs.values(), *app_.state.ask_jobs.values()]
        if not any(j["status"] == "running" for j in jobs):
            try:
                await app_.state.repo.hold_nemotron(0)
            except Exception as error:
                log.warning("could not release Nemotron", extra={"error": str(error)[:200]})

    async def run_lens(app_: FastAPI, job: dict[str, Any]) -> None:
        from factslens.investigator import lens
        from factslens.investigator.evidence import EvidenceGatherer

        def step(index: int, state: str, detail: str = "") -> None:
            job["steps"][index] |= {"state": state, "detail": detail}

        r: Repository = app_.state.repo
        lists: KnownLists = app_.state.lists
        gatherer = EvidenceGatherer()
        try:
            step(0, "running")
            target = await asyncio.to_thread(lens.read_link, job["url"], gatherer, lists)
            kind = {"bluesky": "Bluesky post", "mastodon": "Mastodon post", "telegram": "Telegram post",
                    "article": "web article"}[target.kind]  # fmt: skip
            step(0, "done", f"{kind} by {target.author}, {len(target.images)} picture(s)")
            job["target"] = target.as_dict()

            step(1, "running")
            handle = target.profile.get("handle")
            matches = {m["id"]: m | {"how": list(m["how"])} for m in await r.lens_matches(
                target.post_id, target.author_id, target.match_links, target.match_domains, 72)}  # fmt: skip
            matches |= similar_text(target.text, await r.text_clusters(72), matches)
            for m in matches.values():
                m["campaign"] = await r.campaign_for_cluster(m["id"])
                m["stories"] = [{"id": s["id"], "title": s["title"], "status": s.get("story_status")}
                                for s in await r.stories_for_cluster(m["id"])]  # fmt: skip
            found = sorted(matches.values(), key=lambda m: -(m.get("score") or 0))[:8]
            job["matches"] = found
            step(1, "done", f"found in {len(found)} of our clusters" if found else "not found in our network")

            step(2, "running")
            if target.kind != "article" and handle:
                channel = str(target.profile.get("channel") or "").lower()
                reasons = {
                    "on IFTAS's public list of suspected Portal Kombat accounts": lists.is_known_account(
                        None, handle
                    ),
                    "on a Mastodon server on IFTAS's list": lists.on_listed_server(handle),
                    "a channel the Pravda network republishes": channel in lists.pravda_channels,
                }
                target.profile["listed"] = ", ".join(reason for reason, hit in reasons.items() if hit)
            facts = target.profile
            detail = (
                f"website registered {facts.get('registered') or 'unknown'}"
                if target.kind == "article"
                else f"account {facts.get('age_days', '?')} days old, {facts.get('followers', '?')} followers"
                if target.kind != "telegram"
                else "public Telegram channel"
            )
            step(2, "done", detail + (f"; {facts['listed']}" if facts.get("listed") else ""))

            step(3, "running")
            chat = app_.state.translator
            if chat is None:
                raise RuntimeError("Nemotron is not configured on this server")
            await hold(app_)
            images = [
                d for d in [await asyncio.to_thread(gatherer.image_from_url, u) for u in target.images] if d
            ]
            pack = lens.build_pack(target, found, images)
            result = await asyncio.to_thread(lens.examine, chat, gatherer, lists, pack)
            if not lens.looks_english(target.text):  # one call at a time: NVIDIA rejects overlapping calls
                await hold(app_)
                job["translation"] = await asyncio.to_thread(translate_quietly, chat, target.text)
            if result.report is None:
                raise RuntimeError(result.error or "no report")
            job["report"] = result.report.model_dump()
            job["news"], job["searches"], job["pages"] = result.news, result.searches, result.pages
            job["calls"], job["seconds"], job["dropped"] = result.calls, result.seconds, result.dropped
            step(3, "done", f"{result.calls} model calls, {len(result.searches)} news searches")
            job["status"] = "done"
        except lens.LensError as error:
            job["status"], job["error"] = "failed", str(error)
        except Exception as error:  # the visitor gets a plain message; details go to the log
            log.warning("lens job failed", extra={"error": f"{type(error).__name__}: {error}"[:300]})
            job["status"], job["error"] = (
                "failed",
                "Something went wrong while checking this link. Please try again.",
            )
        for s in job["steps"]:
            if s["state"] == "running":
                s["state"] = "failed"
        gatherer.close()
        await release(app_)

    def translate_quietly(chat: Chat, text: str) -> tuple[str, str] | None:
        try:
            return translate(chat, {"text": text}).get("text")
        except Exception as error:  # a missing translation must not fail the report
            log.warning("lens translation failed", extra={"error": str(error)[:200]})
            return None

    def similar_text(text: str, clusters: list[dict[str, Any]], already: dict[str, Any]) -> dict[str, Any]:
        from factslens.detector import text as textsim

        normalised = textsim.normalise(text)
        if not textsim.is_substantial(normalised):
            return {}
        mine = textsim.signature(normalised)
        hits = {}
        for c in clusters:
            other = textsim.normalise(str(c["label"]))
            if c["id"] in already or not textsim.is_substantial(other):
                continue
            share = textsim.similarity(mine, textsim.signature(other))
            if share >= 0.6:
                hits[c["id"]] = dict(c) | {"how": [f"similar text ({share:.0%})"]}
        return dict(sorted(hits.items(), key=lambda kv: -(kv[1].get("score") or 0))[:5])

    @app.get("/lens", response_class=HTMLResponse)
    async def lens_page(request: Request) -> HTMLResponse:
        return page(request, "lens.html", {"limited": request.query_params.get("limited") == "1"})

    @app.post("/lens", response_model=None)
    async def lens_start(request: Request) -> Response:
        url = parse_qs((await request.body()).decode(errors="replace")).get("url", [""])[0].strip()
        if not url:
            return RedirectResponse("/lens", status_code=303)
        jobs: OrderedDict[str, dict[str, Any]] = request.app.state.lens_jobs
        for job_id, job in reversed(jobs.items()):  # the same link checked recently: show that report
            if (
                job["url"] == url
                and job["status"] != "failed"
                and time.time() - job["started"] < LENS_CACHE_SECONDS
            ):
                return RedirectResponse(f"/lens/{job_id}", status_code=303)
        if not allowed(request):
            return RedirectResponse("/lens?limited=1", status_code=303)
        job_id = secrets.token_urlsafe(9)
        names = [
            "Reading the link",
            "Checking our network: swarms, campaigns, contested stories",
            "Checking the author or website",
            "Nemotron: claims, independent news, persuasion, pictures",
        ]
        jobs[job_id] = {"id": job_id, "url": url, "started": time.time(), "status": "running", "error": None,
                        "steps": [{"name": n, "state": "waiting", "detail": ""} for n in names]}  # fmt: skip
        while len(jobs) > MAX_LENS_JOBS:
            jobs.popitem(last=False)
        task = asyncio.create_task(run_lens(request.app, jobs[job_id]))
        request.app.state.lens_tasks = {*getattr(request.app.state, "lens_tasks", set()), task}
        return RedirectResponse(f"/lens/{job_id}", status_code=303)

    @app.get("/lens/{job_id}.json")
    async def lens_status(request: Request, job_id: str) -> JSONResponse:
        job = request.app.state.lens_jobs.get(job_id)
        if job is None:
            raise HTTPException(404)
        return JSONResponse({"status": job["status"], "steps": job["steps"], "error": job["error"]})

    @app.get("/lens/{job_id}", response_class=HTMLResponse)
    async def lens_report(request: Request, job_id: str) -> HTMLResponse:
        job = request.app.state.lens_jobs.get(job_id)
        if job is None:
            raise HTTPException(404)
        target = job.get("target") or {}
        pictures = [
            {"kind": "image", "thumb": u, "full": u}
            for u in target.get("images") or []
            if u.startswith("https://")
        ]
        return page(
            request,
            "lens_report.html",
            {"job": job, "target": target, "report": job.get("report") or {}, "pictures": pictures,
             "matches": job.get("matches") or [], "labels": LENS_LABELS},
        )  # fmt: skip

    # --- Ask the network ---------------------------------------------------------------------

    async def run_ask(app_: FastAPI, job: dict[str, Any]) -> None:
        from factslens.investigator import ask

        steps = job["steps"]
        try:
            chat = app_.state.translator
            if chat is None:
                raise RuntimeError("Nemotron is not configured on this server")
            steps[0] |= {"state": "done", "detail": "last 72 hours: groups, campaigns, stories"}
            steps[1]["state"] = "running"
            from factslens.investigator.evidence import EvidenceGatherer

            news = EvidenceGatherer()
            tools = ask.Tools(app_.state.repo, app_.state.lists, news)
            await hold(app_)

            def looked(line: str) -> None:
                steps[1]["detail"] = "; ".join(filter(None, [steps[1]["detail"], line]))[-600:]
                asyncio.get_running_loop().create_task(hold(app_))  # still working: keep the hold

            result = await ask.ask(chat, tools, job["question"], on_step=looked)
            steps[1]["state"] = "done"
            if result.answer is None:
                raise RuntimeError(result.error or "no answer")
            steps[2] |= {"state": "done", "detail": f"{result.calls} model calls, {result.seconds:.0f} s"}
            job |= {
                "answer": result.answer.model_dump(),
                "refs": result.registry.items,
                "used": [ask.describe(u) for u in result.used],
                "dropped": result.dropped,
                "calls": result.calls,
                "seconds": result.seconds,
                "status": "done",
            }
            news.close()
        except Exception as error:  # the visitor gets a plain message; details go to the log
            log.warning("ask job failed", extra={"error": f"{type(error).__name__}: {error}"[:300]})
            job["status"], job["error"] = "failed", "Something went wrong while answering. Please try again."
        for s in steps:
            if s["state"] == "running":
                s["state"] = "failed"
        await release(app_)

    @app.get("/ask", response_class=HTMLResponse)
    async def ask_page(request: Request) -> HTMLResponse:
        return page(request, "ask.html", {"limited": request.query_params.get("limited") == "1",
                                          "examples": ASK_EXAMPLES})  # fmt: skip

    @app.post("/ask", response_model=None)
    async def ask_start(request: Request) -> Response:
        question = parse_qs((await request.body()).decode(errors="replace")).get("q", [""])[0]
        question = " ".join(question.split())[:300]
        if len(question) < 3:
            return RedirectResponse("/ask", status_code=303)
        jobs: OrderedDict[str, dict[str, Any]] = request.app.state.ask_jobs
        for job_id, job in reversed(jobs.items()):  # the same question just now: the same answer
            same = job["question"].lower() == question.lower() and job["status"] != "failed"
            if same and time.time() - job["started"] < ASK_CACHE_SECONDS:
                return RedirectResponse(f"/ask/{job_id}", status_code=303)
        if not allowed(request):
            return RedirectResponse("/ask?limited=1", status_code=303)
        job_id = secrets.token_urlsafe(9)
        names = [
            "Reading your question",
            "Nemotron looks through FactsLens's live data",
            "Writing the answer",
        ]
        steps = [{"name": n, "state": "waiting", "detail": ""} for n in names]
        jobs[job_id] = {"id": job_id, "question": question, "started": time.time(), "status": "running",
                        "error": None, "steps": steps}  # fmt: skip
        while len(jobs) > MAX_LENS_JOBS:
            jobs.popitem(last=False)
        task = asyncio.create_task(run_ask(request.app, jobs[job_id]))
        request.app.state.lens_tasks = {*getattr(request.app.state, "lens_tasks", set()), task}
        return RedirectResponse(f"/ask/{job_id}", status_code=303)

    @app.get("/ask/{job_id}.json")
    async def ask_status(request: Request, job_id: str) -> JSONResponse:
        job = request.app.state.ask_jobs.get(job_id)
        if job is None:
            raise HTTPException(404)
        return JSONResponse({"status": job["status"], "steps": job["steps"], "error": job["error"]})

    @app.get("/ask/{job_id}", response_class=HTMLResponse)
    async def ask_answer(request: Request, job_id: str) -> HTMLResponse:
        job = request.app.state.ask_jobs.get(job_id)
        if job is None:
            raise HTTPException(404)
        answer = job.get("answer") or {}
        refs = job.get("refs") or {}
        return page(
            request,
            "ask_answer.html",
            {"job": job, "answer": answer, "refs": refs, "answer_html": cite(answer.get("answer", ""), refs),
             "examples": ASK_EXAMPLES},
        )  # fmt: skip

    @app.get("/graph", response_class=HTMLResponse)
    async def graph_page(request: Request) -> HTMLResponse:
        return page(
            request,
            "graph.html",
            {
                "stories": await repo(request).stories(WINDOW_HOURS),
                "campaigns": await repo(request).campaigns(WINDOW_HOURS),
                "query": str(request.url.query),
            },
        )

    @app.get("/graph/data")
    async def graph_data(
        request: Request, story: int | None = None, campaign: int | None = None, cluster: str | None = None
    ) -> JSONResponse:
        """Nodes and lines for the 3D provenance graph: everything flagged in 24 h, or one story,
        campaign or cluster with its accounts."""
        r = repo(request)
        graph = provenance.Graph()
        story_rows: list[dict[str, Any]] = []
        campaign_rows: list[dict[str, Any]] = []
        focused = True
        if story is not None:
            found = await r.story(story)
            if found is None:
                raise HTTPException(404)
            story_rows = [found]
            ids = list(((found.get("evidence") or {}).get("clusters") or {}).values())
        elif campaign is not None:
            found = await r.campaign(campaign)
            if found is None:
                raise HTTPException(404)
            campaign_rows = [found]
            ids = list(found.get("cluster_ids") or [])[:40]
        elif cluster is not None:
            ids = [cluster, *[row["id"] for row in await r.related(cluster)]]
            story_rows = await r.graph_stories(WINDOW_HOURS, cluster)
        else:
            focused = False
            ids = []
            story_rows = await r.graph_stories(WINDOW_HOURS)
            campaign_rows = await r.campaigns(WINDOW_HOURS)
        clusters = await r.graph_clusters(ids if focused else None, WINDOW_HOURS, 60 if focused else 150)
        cluster_ids = [c["id"] for c in clusters]
        members = await r.graph_members(cluster_ids) if cluster_ids else []
        provenance.add_clusters(
            graph,
            clusters,
            members,
            request.app.state.lists,
            with_accounts=focused,
            reviewer=is_reviewer(request),
            only_listed_websites=story is not None,
        )
        if cluster_ids:
            provenance.add_cluster_links(
                graph, await r.graph_shared(cluster_ids), await r.graph_reuse(cluster_ids)
            )
        for row in story_rows:
            provenance.add_story(graph, row)
        if story is not None:
            provenance.dim_bystanders(graph, story_rows[0])
        for row in campaign_rows:
            provenance.add_campaign(graph, row)
        return JSONResponse(graph.as_dict())

    @app.get("/stories", response_class=HTMLResponse)
    async def story_list(request: Request) -> HTMLResponse:
        return page(
            request,
            "stories.html",
            {"stories": await repo(request).stories(WINDOW_HOURS), "labels": STORY_STATUS},
        )

    @app.get("/stories/{story_id}", response_class=HTMLResponse)
    async def story_page(request: Request, story_id: int) -> HTMLResponse:
        story = await repo(request).story(story_id)
        if story is None:
            raise HTTPException(404)
        evidence = story.get("evidence") or {}
        links: dict[str, str] = {
            ref: h.get("url", "") for ref, h in (evidence.get("headlines") or {}).items()
        }
        links |= {ref: a.get("link", "") for ref, a in (evidence.get("news") or {}).items()}
        links |= {ref: f"/clusters/{cid}" for ref, cid in (evidence.get("clusters") or {}).items()}
        reviewer = is_reviewer(request)
        media = {}  # I1/V1 -> what the browser loads; Telegram photos public, account posts reviewers-only
        for ref, source in (evidence.get("media") or {}).items():
            if isinstance(source, dict) and (reviewer or source.get("public")):
                view = media_view(source.get("kind", "image"), str(source.get("ref", "")), source.get("did"))
                if view:
                    media[ref] = view
        return page(
            request,
            "story.html",
            {
                "s": story,
                "report": story.get("report") or {},
                "evidence": evidence,
                "links": {k: v for k, v in links.items() if v.startswith(("https://", "/clusters/"))},
                "labels": STORY_STATUS,
                "media": media,
            },
        )

    @app.get("/campaigns", response_class=HTMLResponse)
    async def campaign_list(request: Request) -> HTMLResponse:
        return page(
            request,
            "campaigns.html",
            {"campaigns": await repo(request).campaigns(WINDOW_HOURS), "kinds": CAMPAIGN_KINDS},
        )

    @app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
    async def campaign_page(request: Request, campaign_id: int) -> HTMLResponse:
        campaign = await repo(request).campaign(campaign_id)
        if campaign is None:
            raise HTTPException(404)
        evidence = campaign.get("evidence") or {}
        clusters: dict[str, str] = evidence.get("clusters") or {}
        account_hashes: dict[str, str] = evidence.get("accounts") or {}
        known = await repo(request).accounts_by_hash(list(account_hashes.values()))
        reviewer = is_reviewer(request)
        accounts = {}  # A1 -> how to show it: a name only for reviewers and already-public cases
        for ref, account_hash in account_hashes.items():
            a = known.get(account_hash) or {}
            public = bool(a.get("on_public_list")) or a.get("status") in ("unavailable", "external")
            accounts[ref] = {"name": a.get("handle") if (reviewer or public) else None, **a}
        return page(
            request,
            "campaign.html",
            {
                "c": campaign,
                "report": campaign.get("report") or {},
                "clusters": clusters,
                "accounts": accounts,
                "kinds": CAMPAIGN_KINDS,
                "verdict_labels": VERDICT_LABELS,
            },
        )

    @app.post("/clusters/{cluster_id}/accounts/{account_hash}/review", response_model=None)
    async def review_account(request: Request, cluster_id: str, account_hash: str) -> Response:
        """Reviewers: mark one account as an exception to the cluster decision, or clear the mark."""
        if not is_reviewer(request):
            raise HTTPException(403, "reviewer mode required")
        decision = parse_qs((await request.body()).decode(errors="replace")).get("decision", [""])[0]
        if decision not in (*ACCOUNT_MARKS, "clear"):
            raise HTTPException(400, "unknown decision")
        members = await repo(request).members(cluster_id)
        if not any(m.get("account_hash") == account_hash for m in members):
            raise HTTPException(404)
        await repo(request).add_account_review(cluster_id, account_hash, decision)
        return RedirectResponse(f"/clusters/{quote(cluster_id, safe=':')}#accounts", status_code=303)

    @app.post("/clusters/{cluster_id}/translate", response_model=None)
    async def translate_cluster(request: Request, cluster_id: str) -> Response:
        """Reviewers: Nemotron translates the shared message and the sample posts into English."""
        if not is_reviewer(request):
            raise HTTPException(403, "reviewer mode required")
        cluster = await repo(request).cluster(cluster_id)
        if cluster is None:
            raise HTTPException(404)
        chat: Chat | None = request.app.state.translator
        cache: OrderedDict[str, tuple[str, str]] = request.app.state.translations
        texts = {f"label:{cluster_id}": cluster["label"]} | {
            f"post:{p['post_id']}": p.get("text") or "" for p in await repo(request).posts(cluster_id)
        }
        todo = {key: text for key, text in texts.items() if key not in cache and text.strip()}
        failed = chat is None
        if todo and chat is not None:
            try:
                done = await asyncio.to_thread(translate, chat, todo, cluster_id)
            except (BudgetExhausted, OSError, RuntimeError, ValueError) as error:
                log.warning("translation failed", extra={"cluster": cluster_id, "error": str(error)[:200]})
                failed = True
            else:
                for key, value in done.items():
                    cache[key] = value
                    cache.move_to_end(key)
                while len(cache) > MAX_TRANSLATIONS:
                    cache.popitem(last=False)
                failed = not done
        suffix = "?translate=failed#posts" if failed else "#posts"
        return RedirectResponse(f"/clusters/{quote(cluster_id, safe=':')}{suffix}", status_code=303)

    async def next_cluster_redirect(
        request: Request, current: str | None, saved: str | None, sample: bool = False
    ) -> RedirectResponse:
        upcoming = await repo(request).next_unreviewed(current, WINDOW_HOURS, sample=sample)
        if upcoming is None:
            return RedirectResponse("/clusters?reviewed=no", status_code=303)
        params = [f"saved={quote(saved, safe='')}"] if saved else []
        params += ["mode=sample"] if sample else []
        query = "?" + "&".join(params) if params else ""
        return RedirectResponse(f"/clusters/{quote(upcoming, safe=':')}{query}", status_code=303)

    @app.get("/metrics", response_class=HTMLResponse)
    async def metrics_page(request: Request) -> HTMLResponse:
        run = await repo(request).latest_eval_run()
        return page(
            request,
            "metrics.html",
            {
                "run": run,
                "result": (run or {}).get("result"),
                "reviews": review_summary(await repo(request).review_counts()),
                "sample": review_summary(await repo(request).review_counts(sampled_only=True)),
                "review_categories": await repo(request).review_categories(),
                "account_reviews": await repo(request).account_review_counts(),
                "categories": REVIEW_CATEGORIES,
                "nemotron": await repo(request).nemotron_stats(),
                "live": live_numbers(await repo(request).live_findings(), request.app.state.lists),
            },
        )

    @app.get("/review", response_class=HTMLResponse)
    async def review_form(request: Request) -> HTMLResponse:
        return page(request, "review.html", {"failed": False})

    @app.post("/review", response_model=None)
    async def review_login(request: Request) -> Response:
        token = request.app.state.reviewer_token
        submitted = parse_qs((await request.body()).decode(errors="replace")).get("token", [""])[0]
        submitted = submitted.strip().strip("`'\"").strip()  # pasted from chat with spaces or quotes
        if not token or not hmac.compare_digest(submitted.encode(), token.encode()):
            return page(request, "review.html", {"failed": True})
        response = RedirectResponse("/clusters", status_code=303)
        response.set_cookie(
            REVIEW_COOKIE, cookie_value(token), max_age=12 * 3600, httponly=True, secure=True, samesite="lax"
        )
        return response

    @app.get("/review/logout")
    async def review_logout() -> RedirectResponse:
        response = RedirectResponse("/clusters", status_code=303)
        response.delete_cookie(REVIEW_COOKIE)
        return response

    return app


app = create_app()
