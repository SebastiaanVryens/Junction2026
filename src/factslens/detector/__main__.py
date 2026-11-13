"""Run the detector: consume the `posts` topic, keep sliding-window statistics, publish status.

    python -m factslens.detector

M1 skeleton: it measures the stream (volume, top domains and hashtags) and reports consumer
lag. The swarm signals plug into the same loop in M2.
"""

from __future__ import annotations

import logging
import time

import psycopg
from kafka import KafkaConsumer, TopicPartition
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from factslens.common import db
from factslens.common.logging import configure_logging
from factslens.common.models import Event
from factslens.common.settings import get_settings
from factslens.detector.windows import StreamStats
from factslens.ingest.sinks import kafka_client_config

log = logging.getLogger("factslens.detector")

SERVICE = "detector"
REPORT_SECONDS = 5.0


def consumer_lag(consumer: KafkaConsumer) -> int | None:
    """Messages waiting in the topic that we haven't read yet."""
    partitions: set[TopicPartition] = consumer.assignment()
    if not partitions:
        return None
    end = consumer.end_offsets(list(partitions))
    return sum(max(int(end[p]) - int(consumer.position(p)), 0) for p in partitions)


def main() -> None:
    settings = get_settings()
    stats = StreamStats()
    invalid = 0
    consumer = KafkaConsumer(
        settings.kafka_posts_topic,
        **kafka_client_config(settings),
        group_id="factslens-detector",
        auto_offset_reset="latest",
        enable_auto_commit=True,
        max_poll_records=2000,
    )
    # The statistics are rebuilt from the stream, so the detector needs no state of its own:
    # after a restart it continues from the committed offset and the windows refill.
    with psycopg.connect(db.database_url(settings), autocommit=True, connect_timeout=15) as connection:
        log.info("detector started", extra={"topic": settings.kafka_posts_topic})
        last_report = time.monotonic()
        try:
            while True:
                for records in consumer.poll(timeout_ms=1000).values():
                    for record in records:
                        try:
                            stats.add(Event.from_json(record.value))
                        except ValidationError:
                            invalid += 1
                now = time.monotonic()
                if now - last_report >= REPORT_SECONDS:
                    last_report = now
                    data = stats.snapshot() | {"consumer_lag": consumer_lag(consumer), "invalid": invalid}
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
