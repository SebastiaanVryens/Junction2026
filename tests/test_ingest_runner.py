from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from factslens.common.models import Event, EventKind, Platform
from factslens.ingest.cursors import FileCursorStore
from factslens.ingest.runner import run_ingest
from factslens.ingest.sinks import JsonlSink, SinkError, read_jsonl
from factslens.ingest.source import SourceItem

HOUR_US = 3_600_000_000
T0 = 1_763_078_400_000_000  # 2025-11-14 00:00:00 UTC


def make_event(n: int, time_us: int) -> Event:
    return Event(
        id=f"at://did:plc:a/app.bsky.feed.post/{n}",
        platform=Platform.BLUESKY,
        kind=EventKind.POST,
        author="did:plc:a",
        time_us=time_us,
        text=f"post {n}",
    )


class FakeSource:
    name = "fake"

    def __init__(self, items: list[SourceItem]) -> None:
        self._items = items
        self.started_from: int | None = None

    async def items(self, cursor: int | None) -> AsyncIterator[SourceItem]:
        self.started_from = cursor
        for item in self._items:
            if cursor is None or item.cursor > cursor:
                yield item


class MemorySink:
    def __init__(self, fail_flush: bool = False) -> None:
        self.buffered: list[Event] = []
        self.durable: list[Event] = []
        self.closed = False
        self._fail_flush = fail_flush

    async def write(self, event: Event) -> None:
        self.buffered.append(event)

    async def flush(self) -> None:
        if self._fail_flush:
            raise SinkError("broker unavailable")
        self.durable += self.buffered
        self.buffered = []

    async def close(self) -> None:
        self.closed = True


def items(count: int) -> list[SourceItem]:
    return [SourceItem(cursor=T0 + n, event=make_event(n, T0 + n)) for n in range(1, count + 1)]


async def test_stores_everything_and_saves_cursor(tmp_path: Path) -> None:
    source, sink, cursors = FakeSource(items(25)), MemorySink(), FileCursorStore(tmp_path)
    stats = await run_ingest(source, sink, cursors, flush_every=10)
    assert len(sink.durable) == 25 and sink.closed
    assert stats.total == 25
    assert await cursors.load("fake") == T0 + 25


async def test_resumes_from_saved_cursor_not_start_cursor(tmp_path: Path) -> None:
    cursors = FileCursorStore(tmp_path)
    await cursors.save("fake", T0 + 20)
    source, sink = FakeSource(items(25)), MemorySink()
    await run_ingest(source, sink, cursors, start_cursor=T0 + 3)
    assert source.started_from == T0 + 20
    assert [e.id.rsplit("/", 1)[1] for e in sink.durable] == ["21", "22", "23", "24", "25"]


async def test_start_cursor_used_on_first_run(tmp_path: Path) -> None:
    source = FakeSource(items(5))
    await run_ingest(source, MemorySink(), FileCursorStore(tmp_path), start_cursor=T0 + 3)
    assert source.started_from == T0 + 3


async def test_cursor_not_advanced_when_sink_fails(tmp_path: Path) -> None:
    cursors = FileCursorStore(tmp_path)
    sink = MemorySink(fail_flush=True)
    with pytest.raises(SinkError):
        await run_ingest(FakeSource(items(5)), sink, cursors)
    assert await cursors.load("fake") is None  # a restart re-reads these events
    assert sink.closed


async def test_skipped_messages_advance_cursor_and_until_stops(tmp_path: Path) -> None:
    stream = [SourceItem(cursor=T0 + 1, event=None), *items(10)[1:]]
    sink, cursors = MemorySink(), FileCursorStore(tmp_path)
    stats = await run_ingest(FakeSource(stream), sink, cursors, until_us=T0 + 6)
    assert stats.skipped == 1 and stats.total == 4
    assert await cursors.load("fake") == T0 + 5


async def test_jsonl_sink_splits_by_hour_and_survives_restart(tmp_path: Path) -> None:
    first = JsonlSink(tmp_path, prefix="rec")
    await first.write(make_event(1, T0 + 1))
    await first.write(make_event(2, T0 + HOUR_US + 1))
    await first.flush()
    # No close(): simulates a process killed after a flush. A new process appends to the same hour.
    second = JsonlSink(tmp_path, prefix="rec")
    await second.write(make_event(3, T0 + HOUR_US + 2))
    await second.close()

    files = sorted(p.name for p in tmp_path.glob("*.jsonl.gz"))
    assert files == ["rec-20251114-00.jsonl.gz", "rec-20251114-01.jsonl.gz"]
    assert [e.text for e in read_jsonl(tmp_path / files[0])] == ["post 1"]
    assert [e.text for e in read_jsonl(tmp_path / files[1])] == ["post 2", "post 3"]
