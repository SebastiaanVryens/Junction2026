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
from factslens.common.settings import get_settings
from factslens.detector.embed import NvidiaEmbedder
from factslens.enrich.lists import KnownLists
from factslens.investigator.evidence import EvidenceGatherer
from factslens.investigator.llm import ChatClient
from factslens.investigator.stories import now_utc
from factslens.paper.build import PaperSite
from factslens.paper.round import paper_round
from factslens.paper.state import PaperState

log = logging.getLogger("factslens.paper")


def main() -> None:
    parser = argparse.ArgumentParser(description="The Puppet Post, paper edition")
    parser.add_argument("--once", action="store_true", help="one round and one build, then stop")
    parser.add_argument("--build-only", action="store_true", help="only rewrite the pages")
    args = parser.parse_args()
    settings = get_settings()
    state = PaperState.load(settings.resolved(settings.paper_state))
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
