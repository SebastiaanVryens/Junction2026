"""The public web app: pipeline status, candidate clusters and their explainable signals.

    python -m factslens.api --host 0.0.0.0 --port 8000

Privacy: the public sees the shared message, the signals and aggregate account statistics.
Account names and individual posts are shown only in reviewer mode, unlocked with
REVIEWER_TOKEN (CLAUDE.md §7: "show content only when the reviewer needs it").
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from psycopg_pool import AsyncConnectionPool

from factslens import __version__
from factslens.api.metrics import review_summary
from factslens.api.repository import TIERS, PostgresRepository, Repository
from factslens.common import db
from factslens.common.settings import get_settings

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")

# A service is healthy if it reported within this many seconds. Polling sources report only during
# a round (every 5 minutes; a Mastodon round itself takes up to 3), and the investigator only
# between investigations (up to a few minutes each), so they get limits that match.
STALE_AFTER_SECONDS = 60.0
STALE_AFTER_BY_SERVICE = {"telegram": 600.0, "mastodon": 600.0, "investigator": 420.0}
EXPECTED_SERVICES = ("ingest", "telegram", "mastodon", "detector", "enrich", "investigator")
REVIEW_COOKIE = "factslens_review"
WINDOW_HOURS = 24

REVIEW_DECISIONS = {"confirm": "Confirmed", "false_alarm": "False alarm", "unsure": "Unsure"}
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
        "version": __version__,
    }


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


def create_app(repository: Repository | None = None, reviewer_token: str | None = None) -> FastAPI:
    """`repository` and `reviewer_token` are injected in tests; in production they come from settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if repository is not None:
            app.state.repo = repository
            app.state.reviewer_token = reviewer_token
            yield
            return
        settings = get_settings()
        token = settings.reviewer_token.get_secret_value() if settings.reviewer_token else None
        pool = AsyncConnectionPool(
            db.database_url(settings), min_size=1, max_size=4, open=False, kwargs={"autocommit": True}
        )
        await pool.open()
        app.state.repo = PostgresRepository(pool)
        app.state.reviewer_token = token
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
        posts = [
            p
            for p in await repo(request).posts(cluster_id)
            if reviewer or p.get("on_public_list") or p.get("status") in ("unavailable", "external")
        ]
        visible_media: dict[str, dict[str, str | None]] = {}  # media ref -> view, from posts shown here
        for post in posts:
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
                "decisions": REVIEW_DECISIONS,
            },
        )

    @app.post("/clusters/{cluster_id}/review", response_model=None)
    async def review_cluster(request: Request, cluster_id: str) -> Response:
        if not is_reviewer(request):
            raise HTTPException(403, "reviewer mode required")
        form = parse_qs((await request.body()).decode(errors="replace"))
        decision = form.get("decision", [""])[0]
        if decision not in REVIEW_DECISIONS:
            raise HTTPException(400, "unknown decision")
        cluster = await repo(request).cluster(cluster_id)
        if cluster is None:
            raise HTTPException(404)
        investigation = await repo(request).investigation(cluster_id)
        note = form.get("note", [""])[0].strip()[:1000] or None
        await repo(request).add_review(
            cluster_id, decision, note, cluster.get("tier"), (investigation or {}).get("verdict")
        )
        return RedirectResponse(f"/clusters/{cluster_id}#review", status_code=303)

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
                "nemotron": await repo(request).nemotron_stats(),
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
