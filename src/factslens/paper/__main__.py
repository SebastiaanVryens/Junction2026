"""The paper edition's newsroom: every round, compare fresh stories, then write the site as HTML.

python -m factslens.paper              # forever, a round every PAPER_MINUTES
python -m factslens.paper --once       # one round and one build (e.g. a scheduled GitHub job)
python -m factslens.paper --build-only # rewrite the pages from the stored stories, no Nemotron
"""

from __future__ import annotations

import argparse
import logging
import time

from factslens.common.logging import configure_logging
from factslens.common.settings import Settings, get_settings
from factslens.detector.embed import NvidiaEmbedder
from factslens.enrich.lists import KnownLists
from factslens.investigator.evidence import EvidenceGatherer
from factslens.investigator.llm import ChatClient
from factslens.investigator.stories import now_utc
from factslens.paper.build import PaperSite
from factslens.paper.round import paper_round
from factslens.paper.state import PaperState, from_full_edition

log = logging.getLogger("factslens.paper")


def import_full_edition(state: PaperState, settings: Settings, hours: float) -> int:
    """Copy the stories the full edition compared (PostgreSQL), so the paper starts full and old
    story links keep working."""
    import psycopg
    from psycopg.rows import dict_row

    from factslens.common import db

    query = (
        "SELECT id, created_at, title, story_status, report, evidence, model, prompt_version, calls, "
        "prompt_tokens, completion_tokens, latency_ms "
        "FROM stories WHERE status = 'done' AND report IS NOT NULL "
        "AND created_at > now() - make_interval(secs => %s) ORDER BY created_at"
    )
    with psycopg.connect(db.database_url(settings), row_factory=dict_row) as connection:
        rows = connection.execute(query, (hours * 3600,)).fetchall()
    return sum(state.put(from_full_edition(row)) for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="The Puppet Post, paper edition")
    parser.add_argument("--once", action="store_true", help="one round and one build, then stop")
    parser.add_argument("--build-only", action="store_true", help="only rewrite the pages")
    parser.add_argument(
        "--import-hours",
        type=float,
        default=0,
        help="first copy the full edition's stories of the last N hours",
    )
    args = parser.parse_args()
    settings = get_settings()
    state = PaperState.load(settings.resolved(settings.paper_state))
    if args.import_hours:
        log.info("imported", extra={"stories": import_full_edition(state, settings, args.import_hours)})
        state.save()
    lists = KnownLists.load(settings.resolved(settings.ground_truth_dir))
    embed = NvidiaEmbedder.from_settings(settings)
    chat = None if args.build_only else ChatClient.from_settings(settings)
    gatherer = EvidenceGatherer()
    channels_csv = settings.resolved(settings.ground_truth_dir) / "telegram_channels.csv"
    with PaperSite(state, settings.resolved(settings.paper_site), lists, embed) as site:
        while True:
            started = time.monotonic()
            if chat is not None:
                try:
                    count, channels = settings.paper_stories_per_round, settings.paper_telegram_channels
                    paper_round(state, chat, gatherer, lists, embed, channels_csv, count, channels)
                except Exception:  # one bad round must not stop the paper
                    log.exception("paper round failed")
            now = now_utc()
            site.remove(state.prune(now))
            state.save()
            log.info("paper built", extra={"pages": site.build(now), "stories": len(state.stories)})
            if args.once or args.build_only:
                return
            time.sleep(max(60.0, settings.paper_minutes * 60 - (time.monotonic() - started)))


if __name__ == "__main__":
    configure_logging()
    main()
