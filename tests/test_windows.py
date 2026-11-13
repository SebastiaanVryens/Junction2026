from __future__ import annotations

from factslens.common.models import Event, EventKind, Platform
from factslens.detector.windows import MINUTE_US, SlidingCounter, StreamStats, link_domain

T0 = 1_763_078_400_000_000  # on a minute boundary


def test_link_domain_normalises_and_rejects_non_http() -> None:
    assert link_domain("https://WWW.Example.com/a?b=1") == "example.com"
    assert link_domain("http://france.news-pravda.com/x") == "france.news-pravda.com"
    assert link_domain("mailto:a@b.c") is None
    assert link_domain("not a url") is None
    assert link_domain("https://[bad") is None


def test_counts_within_window_and_evicts_old_minutes() -> None:
    counter = SlidingCounter(window_minutes=3)
    for minute in range(5):
        counter.add(T0 + minute * MINUTE_US, "a")
    counter.add(T0 + 4 * MINUTE_US + 5, "b")
    assert counter.totals() == {"a": 3, "b": 1}  # minutes 2, 3, 4
    assert counter.totals(1) == {"a": 1, "b": 1}
    assert counter.per_minute("a", 3) == [1, 1, 1]
    assert counter.per_minute("b", 3) == [0, 0, 1]


def test_late_events_inside_window_count_and_older_ones_do_not() -> None:
    counter = SlidingCounter(window_minutes=3)
    counter.add(T0 + 10 * MINUTE_US, "a")
    counter.add(T0 + 9 * MINUTE_US, "a")  # late but inside the window
    counter.add(T0 + 2 * MINUTE_US, "a")  # far too old
    assert counter.totals() == {"a": 2}


def test_key_cap_bounds_memory() -> None:
    counter = SlidingCounter(window_minutes=2, max_keys_per_minute=2)
    for key in ("a", "b", "c", "a"):
        counter.add(T0, key)
    assert counter.totals() == {"a": 2, "b": 1}
    assert counter.dropped_keys == 1


def post(n: int, minute: int, links: tuple[str, ...] = (), tags: tuple[str, ...] = ()) -> Event:
    return Event(
        id=f"at://did:plc:a/app.bsky.feed.post/{n}",
        platform=Platform.BLUESKY,
        kind=EventKind.POST,
        author="did:plc:a",
        time_us=T0 + minute * MINUTE_US,
        links=links,
        hashtags=tags,
    )


def test_stream_stats_snapshot_is_aggregate_only() -> None:
    stats = StreamStats(window_minutes=60)
    # The same domain twice in one post counts once; hashtags likewise.
    stats.add(post(1, 0, links=("https://a.com/1", "https://www.a.com/2"), tags=("x", "x")))
    stats.add(post(2, 1, links=("https://a.com/3", "https://b.org/"), tags=("x",)))
    repost = post(3, 1).model_copy(update={"kind": EventKind.REPOST, "subject": "at://x/y/z"})
    stats.add(repost)

    snapshot = stats.snapshot()
    assert snapshot["total"] == 3
    assert snapshot["events_last_hour"] == {"post": 2, "repost": 1}
    assert snapshot["events_last_minute"] == {"post": 1, "repost": 1}
    assert snapshot["top_domains_10m"] == [("a.com", 2), ("b.org", 1)]
    assert snapshot["top_hashtags_10m"] == [("x", 2)]
    assert "did:plc" not in str(snapshot)
