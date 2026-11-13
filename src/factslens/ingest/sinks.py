"""Where ingested events go: Aiven Kafka (the live pipeline) and/or gzip JSONL files (recordings)."""

from __future__ import annotations

import asyncio
import gzip
import logging
import threading
import zlib
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from factslens.common.models import Event
from factslens.common.settings import MissingSettingError, Settings

log = logging.getLogger(__name__)


class SinkError(RuntimeError):
    """Events could not be stored. The runner must not advance the cursor past them."""


class Sink(Protocol):
    async def write(self, event: Event) -> None: ...

    async def flush(self) -> None:
        """Make everything written so far durable, or raise SinkError."""
        ...

    async def close(self) -> None: ...


def kafka_client_config(settings: Settings) -> dict[str, Any]:
    """Connection settings shared by producers and consumers (SASL/SCRAM over TLS, as Aiven documents)."""
    missing = [
        name
        for name, value in (
            ("KAFKA_BOOTSTRAP", settings.kafka_bootstrap),
            ("KAFKA_CA_FILE", settings.kafka_ca_file),
            ("KAFKA_SASL_USERNAME", settings.kafka_sasl_username),
            ("KAFKA_SASL_PASSWORD", settings.kafka_sasl_password),
        )
        if not value
    ]
    if missing:
        raise MissingSettingError(*missing)
    assert settings.kafka_ca_file and settings.kafka_sasl_password
    return {
        "bootstrap_servers": settings.kafka_bootstrap,
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "SCRAM-SHA-256",
        "sasl_plain_username": settings.kafka_sasl_username,
        "sasl_plain_password": settings.kafka_sasl_password.get_secret_value(),
        "ssl_cafile": str(settings.resolved(settings.kafka_ca_file)),
    }


class KafkaSink:
    """Produces events to one topic. `write` only buffers; `flush` waits for broker acknowledgement."""

    def __init__(self, settings: Settings, topic: str | None = None) -> None:
        from kafka import KafkaProducer

        self._topic = topic or settings.kafka_posts_topic
        self._producer = KafkaProducer(
            **kafka_client_config(settings),
            acks="all",
            compression_type="gzip",
            linger_ms=200,
            batch_size=256 * 1024,
            retries=5,
        )
        self._lock = threading.Lock()
        self._failures: list[str] = []

    def _on_error(self, error: BaseException) -> None:
        with self._lock:
            self._failures.append(f"{type(error).__name__}: {error}")

    async def write(self, event: Event) -> None:
        future = self._producer.send(self._topic, key=event.author.encode(), value=event.to_json())
        future.add_errback(self._on_error)

    async def flush(self) -> None:
        try:
            await asyncio.to_thread(self._producer.flush, 30)
        except Exception as error:
            raise SinkError(f"kafka flush failed: {type(error).__name__}: {error}") from error
        with self._lock:
            failures, self._failures = self._failures, []
        if failures:
            raise SinkError(f"{len(failures)} kafka sends failed; first: {failures[0]}")

    async def close(self) -> None:
        await asyncio.to_thread(self._producer.close, 30)


class JsonlSink:
    """Appends events to one gzip JSONL file per UTC hour of event time: `<prefix>-YYYYMMDD-HH.jsonl.gz`.

    Each flush appends one complete gzip member in a single write, so a file is never left with
    a half-written block in the middle: a killed process can be restarted and keep appending.
    """

    def __init__(self, directory: Path, prefix: str = "events") -> None:
        self._directory = directory
        self._prefix = prefix
        self._pending: dict[str, list[bytes]] = {}
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, hour: str) -> Path:
        return self._directory / f"{self._prefix}-{hour}.jsonl.gz"

    async def write(self, event: Event) -> None:
        hour = datetime.fromtimestamp(event.time_us / 1_000_000, UTC).strftime("%Y%m%d-%H")
        self._pending.setdefault(hour, []).append(event.to_json() + b"\n")

    async def flush(self) -> None:
        pending, self._pending = self._pending, {}
        try:
            for hour, lines in pending.items():
                member = gzip.compress(b"".join(lines), compresslevel=6)
                with self._path(hour).open("ab") as handle:
                    handle.write(member)
        except OSError as error:
            raise SinkError(f"file write failed: {error}") from error

    async def close(self) -> None:
        await self.flush()


class MultiSink:
    def __init__(self, sinks: Sequence[Sink]) -> None:
        self._sinks = tuple(sinks)

    async def write(self, event: Event) -> None:
        for sink in self._sinks:
            await sink.write(event)

    async def flush(self) -> None:
        for sink in self._sinks:
            await sink.flush()

    async def close(self) -> None:
        for sink in self._sinks:
            await sink.close()


def read_jsonl(path: Path) -> Iterator[Event]:
    """Read a recording made by JsonlSink. Tolerates a file cut off mid-write."""
    try:
        with gzip.open(path, "rb") as handle:
            for line in handle:
                if line.endswith(b"\n"):
                    yield Event.from_json(line)
    except (EOFError, gzip.BadGzipFile, zlib.error) as error:
        log.warning(
            "recording is damaged; read up to the last complete block",
            extra={"path": str(path), "error": f"{type(error).__name__}: {error}"},
        )
