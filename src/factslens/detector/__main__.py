"""Run the detector: consume the `posts` topic, find candidate swarms, store them, publish status.

    python -m factslens.detector

Stage 1 runs here (factslens.detector.groups). Candidates go to PostgreSQL, where stage 2
(factslens.enrich) adds account profiles and scores them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import psycopg
from kafka import ConsumerRebalanceListener, KafkaConsumer, TopicPartition
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from factslens.common import db, store
from factslens.common.logging import configure_logging
from factslens.common.models import Event
from factslens.common.privacy import AccountHasher
from factslens.common.settings import get_settings
from factslens.detector.embed import NvidiaEmbedder
from factslens.detector.groups import SwarmDetector
from factslens.detector.windows import StreamStats
from factslens.ingest.sinks import kafka_client_config

log = logging.getLogger("factslens.detector")

SERVICE = "detector"
REPORT_SECONDS = 5.0
RETENTION_SECONDS = 3600.0


def consumer_lag(consumer: KafkaConsumer) -> int | None:
    """Messages waiting in the topic that we haven't read yet."""
    partitions: set[TopicPartition] = consumer.assignment()
    if not partitions:
        return None
    end = consumer.end_offsets(list(partitions))
    return sum(max(int(end[p]) - int(consumer.position(p)), 0) for p in partitions)


async def _migrate() -> None:
    connection = await db.connect(get_settings())
    try:
        await db.migrate(connection)
    finally:
        await connection.close()


WARM_START_MINUTES = 60  # the detector's window: rebuilt from Kafka on every start


class WarmStart(ConsumerRebalanceListener):  # type: ignore[misc]
    """On the first partition assignment, go back WARM_START_MINUTES in the stream, so a restart
    (a deploy) does not empty the detector's one-hour windows. Kafka keeps 24 hours; the stored
    clusters are updated in place and duplicate posts are skipped, so reading again is harmless."""

    def __init__(self, consumer: Any, minutes: int = WARM_START_MINUTES, clock: Any = time.time) -> None:
        self.consumer = consumer
        self.minutes = minutes
        self.clock = clock
        self.done = False
        self.assigned: set[Any] = set()

    def on_partitions_revoked(self, revoked: Any) -> None:
        pass

    def on_partitions_assigned(self, assigned: Any) -> None:
        # Only note it here: this runs inside the client's own poll, where another request to the
        # broker can deadlock (it did, 14 Nov). The rewind happens in `apply`, after poll returns.
        if not self.done and assigned:
            self.assigned = set(assigned)

    def apply(self) -> None:
        """Rewind once, from the main loop."""
        if self.done or not self.assigned:
            return
        self.done = True
        since_ms = int((self.clock() - self.minutes * 60) * 1000)
        try:
            found = self.consumer.offsets_for_times({tp: since_ms for tp in self.assigned})
        except Exception as error:  # an old broker or a timeout: continue from the committed offset
            log.warning("warm start skipped", extra={"error": str(error)[:200]})
            return
        for tp, offset in found.items():
            if offset is not None:
                self.consumer.seek(tp, offset.offset)
        log.info("warm start", extra={"minutes": self.minutes, "partitions": len(found)})


def main() -> None:
    settings = get_settings()
    hasher = AccountHasher.from_settings(settings)
    db.use_compatible_event_loop()
    asyncio.run(_migrate())

    stats = StreamStats()
    embedder = NvidiaEmbedder.from_settings(settings) if settings.translated_groups else None
    detector = SwarmDetector(embedder=embedder)
    log.info("translated groups", extra={"enabled": embedder is not None})
    counters = {"invalid": 0, "snapshots": 0, "write_errors": 0}
    consumer = KafkaConsumer(
        **kafka_client_config(settings),
        group_id="factslens-detector",
        auto_offset_reset="latest",
        enable_auto_commit=True,
        max_poll_records=2000,
    )
    # Windows are rebuilt from the stream: on start the detector reads the last hour again
    # (WarmStart), so its groups are full at once; stored clusters are updated in place.
    warm = WarmStart(consumer)
    consumer.subscribe([settings.kafka_posts_topic], listener=warm)
    with psycopg.connect(db.database_url(settings), autocommit=True, connect_timeout=15) as connection:
        log.info("detector started", extra={"topic": settings.kafka_posts_topic})
        last_report = time.monotonic()
        last_retention = 0.0
        try:
            while True:
                batches = consumer.poll(timeout_ms=1000)
                if not warm.done and warm.assigned:
                    warm.apply()
                    continue  # read again from the rewound position
                for records in batches.values():
                    for record in records:
                        try:
                            event = Event.from_json(record.value)
                        except ValidationError:
                            counters["invalid"] += 1
                            continue
                        stats.add(event)
                        for snapshot in detector.add(event):
                            try:
                                store.save_snapshot(connection, snapshot, hasher)
                                counters["snapshots"] += 1
                            except psycopg.Error as error:
                                counters["write_errors"] += 1
                                log.warning(
                                    "could not store candidate",
                                    extra={"cluster": snapshot.key, "error": str(error)[:300]},
                                )
                now = time.monotonic()
                if now - last_retention >= RETENTION_SECONDS:
                    last_retention = now
                    log.info("retention applied", extra=store.apply_retention(connection))
                if now - last_report >= REPORT_SECONDS:
                    last_report = now
                    data = (
                        stats.snapshot()
                        | {"consumer_lag": consumer_lag(consumer)}
                        | counters
                        | {"detector_state": detector.memory_summary()}
                    )
                    connection.execute(
                        """
                        INSERT INTO service_status (service, updated_at, data) VALUES (%s, now(), %s)
                        ON CONFLICT (service) DO UPDATE SET updated_at = now(), data = EXCLUDED.data
                        """,
                        (SERVICE, Jsonb(data)),
                    )
        except KeyboardInterrupt:
            log.info("stopped by user")
        finally:
            consumer.close()


if __name__ == "__main__":
    configure_logging()
    main()
