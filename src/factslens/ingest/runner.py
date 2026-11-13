"""The ingest loop: source -> sink, saving the cursor only after the sink confirmed the data."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from factslens.ingest.cursors import CursorStore
from factslens.ingest.sinks import Sink
from factslens.ingest.source import Source

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    started_at: float = field(default_factory=time.time)
    events: Counter[str] = field(default_factory=Counter)  # stored events by kind
    skipped: int = 0  # messages we don't ingest (deletes, account events, malformed records)
    last_time_us: int | None = None  # source time of the newest stored-or-skipped message
    saved_cursor: int | None = None

    @property
    def total(self) -> int:
        return sum(self.events.values())

    def lag_seconds(self, now: float | None = None) -> float | None:
        """How far behind the live stream we are (large while replaying history)."""
        if self.last_time_us is None:
            return None
        return (now if now is not None else time.time()) - self.last_time_us / 1_000_000

    def snapshot(self) -> dict[str, object]:
        lag = self.lag_seconds()
        return {
            "events": dict(self.events),
            "total": self.total,
            "skipped": self.skipped,
            "last_time_us": self.last_time_us,
            "saved_cursor": self.saved_cursor,
            "lag_seconds": round(lag, 1) if lag is not None else None,
            "uptime_seconds": round(time.time() - self.started_at),
        }


Reporter = Callable[[IngestStats], Awaitable[None]]


async def run_ingest(
    source: Source,
    sink: Sink,
    cursors: CursorStore,
    *,
    job: str | None = None,
    start_cursor: int | None = None,
    until_us: int | None = None,
    flush_every: int = 5000,
    flush_seconds: float = 2.0,
    report_seconds: float = 10.0,
    reporter: Reporter | None = None,
    stats: IngestStats | None = None,
) -> IngestStats:
    """Run until cancelled, or until the source passes `until_us`.

    Resume order: the saved cursor for `job` wins; `start_cursor` is only used on the first run.
    A cursor is saved only after `sink.flush()` succeeded, so a crash re-reads at most one batch
    (at-least-once delivery; events have unique ids).
    """
    job = job or source.name
    stats = stats or IngestStats()
    saved = await cursors.load(job)
    cursor = saved if saved is not None else start_cursor
    log.info("ingest starting", extra={"job": job, "cursor": cursor, "resumed": saved is not None})

    pending = 0
    newest: int | None = None
    last_flush = last_report = time.monotonic()

    async def commit() -> None:
        nonlocal pending, last_flush
        await sink.flush()
        if newest is not None:
            await cursors.save(job, newest)
            stats.saved_cursor = newest
        pending = 0
        last_flush = time.monotonic()

    try:
        async for item in source.items(cursor):
            if until_us is not None and item.cursor >= until_us:
                break
            newest = item.cursor
            stats.last_time_us = item.cursor
            if item.event is None:
                stats.skipped += 1
            else:
                await sink.write(item.event)
                stats.events[item.event.kind.value] += 1
                pending += 1

            now = time.monotonic()
            if pending >= flush_every or now - last_flush >= flush_seconds:
                await commit()
            if now - last_report >= report_seconds:
                last_report = now
                log.info("ingest progress", extra={"job": job, **stats.snapshot()})
                if reporter is not None:
                    await reporter(stats)
    finally:
        # Runs on normal exit, Ctrl+C and cancellation: store what we have before stopping.
        try:
            await commit()
        finally:
            await sink.close()
        log.info("ingest stopped", extra={"job": job, **stats.snapshot()})
    return stats
