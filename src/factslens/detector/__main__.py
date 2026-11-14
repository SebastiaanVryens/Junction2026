"""Run the detector: consume the `posts` topic, find candidate swarms, store them, publish status.

    python -m factslens.detector

Stage 1 runs here (factslens.detector.groups). Candidates go to PostgreSQL, where stage 2
(factslens.enrich) adds account profiles and scores them.
"""

from __future__ import annotations

import asyncio
import logging
import time

import psycopg
from kafka import KafkaConsumer, TopicPartition
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from factslens.common import db, store
from factslens.common.logging import configure_logging
from factslens.common.models import Event
from factslens.common.privacy import AccountHasher
from factslens.common.settings import get_settings
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


def main() -> None:
    settings = get_settings()
    hasher = AccountHasher.from_settings(settings)
    db.use_compatible_event_loop()
    asyncio.run(_migrate())

    stats = StreamStats()
    detector = SwarmDetector()
    counters = {"invalid": 0, "snapshots": 0, "write_errors": 0}
    consumer = KafkaConsumer(
        settings.kafka_posts_topic,
        **kafka_client_config(settings),
        group_id="factslens-detector",
        auto_offset_reset="latest",
        enable_auto_commit=True,
        max_poll_records=2000,
    )
    # Windows are rebuilt from the stream, so after a restart the detector continues from the
    # committed offset and its groups refill; stored clusters are updated in place.
    with psycopg.connect(db.database_url(settings), autocommit=True, connect_timeout=15) as connection:
        log.info("detector started", extra={"topic": settings.kafka_posts_topic})
        last_report = time.monotonic()
        last_retention = 0.0
        try:
            while True:
                for records in consumer.poll(timeout_ms=1000).values():
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
