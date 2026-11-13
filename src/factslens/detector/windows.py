"""Sliding-window counters over event time: the building block for burst and reuse signals.

Everything is bucketed per minute of *event* time (not arrival time), so replaying a
recording produces the same numbers as the live run did. Memory is bounded: buckets older
than the window are dropped, and each bucket keeps at most `max_keys` distinct keys.
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlsplit

from factslens.common.models import Event, EventKind

MINUTE_US = 60_000_000


def link_domain(url: str) -> str | None:
    """Host of a link, lower-cased, without 'www.'. None for anything that isn't http(s)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname.lower().removeprefix("www.")


class SlidingCounter:
    """Counts keys per minute and answers "how many in the last N minutes?"."""

    def __init__(self, window_minutes: int, max_keys_per_minute: int = 50_000) -> None:
        self._window = window_minutes
        self._max_keys = max_keys_per_minute
        self._buckets: dict[int, Counter[str]] = {}
        self._newest: int | None = None
        self.dropped_keys = 0  # distinct keys not counted because a minute hit max_keys

    def add(self, time_us: int, key: str, count: int = 1) -> None:
        minute = time_us // MINUTE_US
        if self._newest is None or minute > self._newest:
            self._newest = minute
            self._evict()
        elif minute <= self._newest - self._window:
            return  # older than the window (late or replayed event)
        bucket = self._buckets.setdefault(minute, Counter())
        if key not in bucket and len(bucket) >= self._max_keys:
            self.dropped_keys += 1
            return
        bucket[key] += count

    def _evict(self) -> None:
        assert self._newest is not None
        oldest = self._newest - self._window
        for minute in [m for m in self._buckets if m <= oldest]:
            del self._buckets[minute]

    def totals(self, minutes: int | None = None) -> Counter[str]:
        """Counts per key over the last `minutes` (default: the whole window), ending at the newest event."""
        if self._newest is None:
            return Counter()
        span = min(minutes or self._window, self._window)
        total: Counter[str] = Counter()
        for minute, bucket in self._buckets.items():
            if minute > self._newest - span:
                total.update(bucket)
        return total

    def per_minute(self, key: str, minutes: int) -> list[int]:
        """Counts for one key, oldest minute first, ending at the newest event's minute."""
        if self._newest is None:
            return []
        return [
            self._buckets.get(m, Counter())[key] for m in range(self._newest - minutes + 1, self._newest + 1)
        ]


class StreamStats:
    """What the M1 detector tracks: volume by kind, and the most shared domains and hashtags."""

    def __init__(self, window_minutes: int = 60) -> None:
        self.kinds = SlidingCounter(window_minutes, max_keys_per_minute=16)
        self.domains = SlidingCounter(window_minutes)
        self.hashtags = SlidingCounter(window_minutes)
        self.total = 0
        self.synthetic = 0
        self.last_time_us: int | None = None

    def add(self, event: Event) -> None:
        self.total += 1
        self.synthetic += event.synthetic
        self.last_time_us = max(self.last_time_us or 0, event.time_us)
        self.kinds.add(event.time_us, event.kind.value)
        if event.kind is EventKind.POST:
            for domain in {d for url in event.links if (d := link_domain(url))}:
                self.domains.add(event.time_us, domain)
            for tag in set(event.hashtags):
                self.hashtags.add(event.time_us, tag)

    def snapshot(self, top: int = 15) -> dict[str, object]:
        """Aggregates only: safe to show publicly (no account identifiers)."""
        last_minutes = 10
        return {
            "total": self.total,
            "synthetic": self.synthetic,
            "last_time_us": self.last_time_us,
            "events_last_minute": dict(self.kinds.totals(1)),
            "events_last_hour": dict(self.kinds.totals()),
            "posts_per_minute": self.kinds.per_minute(EventKind.POST.value, 30),
            "top_domains_10m": self.domains.totals(last_minutes).most_common(top),
            "top_hashtags_10m": self.hashtags.totals(last_minutes).most_common(top),
        }
