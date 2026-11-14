"""Run an ingest job.

python -m factslens.ingest                         # live pipeline: Jetstream -> Kafka, cursor in Postgres
python -m factslens.ingest --sink file --cursor-store file --job recorder --since-hours 36
                                                   # record the stream to data/recordings/ (no Kafka/Postgres)
python -m factslens.ingest --source telegram --job telegram
                                                   # public Telegram channels -> Kafka (same topic)
python -m factslens.ingest --source mastodon --job mastodon
                                                   # public timelines of IFTAS-listed Mastodon servers
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import time

from factslens.common import db
from factslens.common.logging import configure_logging
from factslens.common.settings import Settings, get_settings
from factslens.ingest.cursors import CursorStore, FileCursorStore, PostgresCursorStore
from factslens.ingest.jetstream import COLLECTIONS, DEFAULT_COLLECTIONS, JetstreamSource
from factslens.ingest.mastodon import MastodonSource, listed_servers
from factslens.ingest.runner import IngestStats, Reporter, run_ingest
from factslens.ingest.sinks import JsonlSink, KafkaSink, MultiSink, Sink
from factslens.ingest.source import Source
from factslens.ingest.telegram import TelegramSource, pravda_source_channels

log = logging.getLogger("factslens.ingest")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FactsLens ingest: Bluesky Jetstream -> Kafka and/or files.")
    parser.add_argument("--source", choices=("jetstream", "telegram", "mastodon"), default="jetstream")
    parser.add_argument(
        "--telegram-top", type=int, default=40, help="watch the N channels Pravda republishes most"
    )
    parser.add_argument("--sink", choices=("kafka", "file", "both"), default="kafka")
    parser.add_argument("--cursor-store", choices=("postgres", "file"), default="postgres")
    parser.add_argument("--job", default="ingest", help="name the cursor and status are stored under")
    parser.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="on the first run, start this many hours in the past (Jetstream keeps about 36 h)",
    )
    parser.add_argument(
        "--collections", nargs="+", default=list(DEFAULT_COLLECTIONS), choices=sorted(COLLECTIONS)
    )
    return parser.parse_args()


def build_sink(kind: str, settings: Settings) -> Sink:
    sinks: list[Sink] = []
    if kind in ("kafka", "both"):
        sinks.append(KafkaSink(settings))
    if kind in ("file", "both"):
        sinks.append(JsonlSink(settings.data_dir / "recordings"))
    return sinks[0] if len(sinks) == 1 else MultiSink(sinks)


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    source: Source
    if args.source == "telegram":
        channels = settings.telegram_channel_list() or pravda_source_channels(
            settings.resolved(settings.ground_truth_dir) / "telegram_channels.csv", args.telegram_top
        )
        source = TelegramSource(channels, poll_seconds=settings.telegram_poll_seconds)
        log.info("watching telegram channels", extra={"channels": len(channels)})
    elif args.source == "mastodon":
        servers = settings.mastodon_server_list()
        instances = settings.resolved(settings.ground_truth_dir) / "instances.csv"
        if not servers and instances.is_file():
            with instances.open(encoding="utf-8", newline="") as handle:
                servers = listed_servers(csv.DictReader(handle))
        source = MastodonSource(servers, poll_seconds=settings.mastodon_poll_seconds)
        log.info("watching mastodon servers", extra={"servers": len(servers)})
    else:
        source = JetstreamSource(settings.jetstream_url_list(), args.collections)
    start_cursor = int((time.time() - args.since_hours * 3600) * 1_000_000) if args.since_hours else None

    cursors: CursorStore
    reporter: Reporter | None = None
    connection: db.Connection | None = None
    if args.cursor_store == "postgres":
        connection = await db.connect(settings)
        await db.migrate(connection)
        cursors = PostgresCursorStore(connection)

        async def report(stats: IngestStats) -> None:
            assert connection is not None
            extra = {
                name: getattr(source, attr)
                for name, attr in (
                    ("reconnects", "reconnects"),
                    ("polls", "polls"),
                    ("errors", "errors"),
                    ("skipped_bridged", "skipped_bridged"),
                )
                if hasattr(source, attr)
            }
            if hasattr(source, "skipped_us"):
                extra["skipped_seconds"] = source.skipped_us / 1e6
            await db.write_status(connection, args.job, stats.snapshot() | extra)

        reporter = report
    else:
        cursors = FileCursorStore(settings.data_dir / "cursors")

    try:
        await run_ingest(
            source,
            build_sink(args.sink, settings),
            cursors,
            job=args.job,
            start_cursor=start_cursor,
            reporter=reporter,
        )
    finally:
        if connection is not None:
            await connection.close()


if __name__ == "__main__":
    configure_logging()
    db.use_compatible_event_loop()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("stopped by user")
