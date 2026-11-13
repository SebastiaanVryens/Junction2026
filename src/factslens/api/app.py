"""The public web app. M1: health endpoint and a live pipeline status page.

uvicorn factslens.api.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from psycopg_pool import AsyncConnectionPool

from factslens import __version__
from factslens.common import db
from factslens.common.settings import get_settings

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")

# A service is healthy if it reported within this many seconds.
STALE_AFTER_SECONDS = 60.0
EXPECTED_SERVICES = ("ingest", "detector")

StatusProvider = Callable[[], Awaitable[dict[str, db.ServiceStatus]]]


def build_view(statuses: dict[str, db.ServiceStatus], now: datetime | None = None) -> dict[str, Any]:
    """Everything the status page and /health show, computed in one place."""
    now = now or datetime.now(UTC)
    services: dict[str, dict[str, Any]] = {}
    for name in EXPECTED_SERVICES:
        status = statuses.get(name)
        age = (now - status.updated_at).total_seconds() if status else None
        services[name] = {
            "reported_seconds_ago": round(age, 1) if age is not None else None,
            "healthy": age is not None and age <= STALE_AFTER_SECONDS,
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


def create_app(provider: StatusProvider | None = None) -> FastAPI:
    """`provider` is injected in tests; in production statuses come from PostgreSQL."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if provider is not None:
            app.state.statuses = provider
            yield
            return
        pool = AsyncConnectionPool(
            db.database_url(get_settings()), min_size=1, max_size=3, open=False, kwargs={"autocommit": True}
        )
        await pool.open()

        async def from_database() -> dict[str, db.ServiceStatus]:
            async with pool.connection() as connection:
                return await db.read_statuses(connection)

        app.state.statuses = from_database
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="FactsLens", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)

    async def view(request: Request) -> dict[str, Any]:
        try:
            return build_view(await request.app.state.statuses())
        except Exception as error:  # the page must render even when the database is down
            return build_view({}) | {"error": f"status store unavailable: {type(error).__name__}"}

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
        return TEMPLATES.TemplateResponse(request, "status.html", await view(request))

    @app.get("/status/fragment", response_class=HTMLResponse)
    async def status_fragment(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, "_status.html", await view(request))

    return app


app = create_app()
