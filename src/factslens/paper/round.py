"""One round of the paper edition: pick fresh stories and compare how everyone tells them.

The same story code as the full edition (factslens.investigator.stories), without social media and
without a database: no groups of accounts, and the results go into the paper state."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from factslens.enrich.lists import KnownLists
from factslens.ingest.telegram import pravda_source_channels
from factslens.investigator import stories
from factslens.investigator.__main__ import story_media
from factslens.investigator.evidence import EvidenceGatherer
from factslens.investigator.llm import ChatClient
from factslens.paper.state import PaperState

log = logging.getLogger(__name__)
COVERED_HOURS = 18  # titles compared this recently are not picked again


def paper_round(
    state: PaperState,
    chat: ChatClient,
    gatherer: EvidenceGatherer,
    lists: KnownLists,
    embed: Any,
    channels_csv: Path,
    count: int,
    telegram_channels: int,
) -> int:
    """Compare up to `count` fresh stories; each finished story is saved at once. Returns how many."""
    now = stories.now_utc()
    channels = pravda_source_channels(channels_csv, telegram_channels)
    with httpx.Client(timeout=15, headers={"User-Agent": stories.USER_AGENT}) as client:
        headlines = (
            stories.fetch_state_media(client, now)
            + stories.fetch_pravda_sites(client, now)
            + stories.fetch_telegram(client, channels, now)
        )
    covered = [s["title"] for s in state.since(COVERED_HOURS, now)]
    picks = stories.drop_repeats(stories.pick_stories(chat, headlines, covered, count), covered, embed)
    log.info("paper round", extra={"headlines": len(headlines), "picked": [p.title for p in picks]})
    by_ref = {f"H{n}": h for n, h in enumerate(headlines, 1)}
    done = 0
    for pick in picks:
        news = stories.search_coverage(gatherer, pick, lists)
        media = story_media(gatherer, [by_ref[r] for r in pick.refs], [])
        pack = stories.build_story_pack(pick, headlines, news, None, media)
        result = stories.compare(chat, pick.title, pack)
        if result.report is None:
            log.warning("story not compared", extra={"title": pick.title, "error": result.error})
            continue
        story_id = state.add(
            {
                "title": result.report.title,
                "story_status": result.report.status,
                "cluster_ids": [],
                "report": result.report.model_dump(mode="json"),
                "evidence": pack.evidence | {"dropped": result.dropped[:20]},
                "model": chat.model,
                "prompt_version": stories.PROMPT_VERSION,
                "calls": result.calls,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "latency_ms": result.latency_ms,
            },
            stories.now_utc(),
        )
        state.save()  # a crash later in the round keeps what is done
        log.info("story stored", extra={"id": story_id, "title": pick.title, "status": result.report.status})
        done += 1
    return done
