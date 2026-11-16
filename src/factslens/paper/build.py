"""Write the paper edition as plain HTML files, rendered by the full site's own code and templates.

The site runs in-process (paper-only mode) over the paper state; every page is fetched once and
saved where a static web server (nginx, GitHub Pages) finds it: /stories/1001 -> stories/1001/index.html."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, cast

from fastapi.testclient import TestClient

from factslens.api.app import create_app, find_source_picture
from factslens.api.repository import Repository
from factslens.enrich.lists import KnownLists
from factslens.paper.repository import StateRepository
from factslens.paper.state import PaperState

log = logging.getLogger(__name__)
RENDER_HOURS = 48  # story pages re-rendered each build (fresh pictures); older pages stay as written
STATIC = Path(__file__).resolve().parent.parent / "api" / "static"
PAGES = {"/": "index.html", "/stories": "stories/index.html", "/how-we-work": "how-we-work/index.html",
         "/robots.txt": "robots.txt"}  # fmt: skip


def write(path: Path, content: bytes) -> None:
    """Atomically: a reader never gets a half-written page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)


class PaperSite:
    """One in-process copy of the site for the service's lifetime, so pictures stay cached between builds."""

    def __init__(
        self, state: PaperState, out: Path, lists: KnownLists, embedder: Any = None, find_picture: Any = None
    ) -> None:
        self.state, self.out = state, out
        self.app = create_app(cast(Repository, StateRepository(state)), lists=lists, paper_only=True)
        self.client = TestClient(self.app)
        self.embedder, self.find_picture = embedder, find_picture or find_source_picture

    def __enter__(self) -> PaperSite:
        self.client.__enter__()
        self.app.state.embedder = self.embedder  # one event, one story
        self.app.state.picture_finder = self.find_picture
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.client.__exit__(kind, error, trace)

    def build(self, now: datetime) -> int:
        """Write every page; returns how many were written."""
        self.app.state.edition_count = len(self.state.since(24, now))
        self.app.state.page_cache.clear()
        pages = dict(PAGES)
        for story in self.state.stories:
            target = f"stories/{story['id']}/index.html"
            recent = story in self.state.since(RENDER_HOURS, now)
            if recent or not (self.out / target).is_file():
                pages[f"/stories/{story['id']}"] = target
        written = 0
        for url, target in pages.items():
            response = self.client.get(url)
            if response.status_code != 200:
                log.warning("page not written", extra={"url": url, "status": response.status_code})
                continue
            write(self.out / target, response.content)
            written += 1
        missing = self.client.get("/this-page-is-not-in-the-paper")
        write(self.out / "404.html", missing.content)
        shutil.copytree(STATIC, self.out / "static", dirs_exist_ok=True)
        return written

    def remove(self, story_ids: list[int]) -> None:
        for story_id in story_ids:
            shutil.rmtree(self.out / "stories" / str(story_id), ignore_errors=True)
