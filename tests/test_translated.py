# ruff: noqa: RUF001  (Cyrillic and Finnish test texts on purpose)
"""The same claim in several languages: linking by meaning, groups, gates, the embedding client."""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np

from factslens.common.models import Event, EventKind, Platform
from factslens.detector.embed import NvidiaEmbedder
from factslens.detector.groups import GroupKind, SwarmDetector
from factslens.investigator.llm import Budget

CLAIM = np.array([1.0, 0.0, 0.0])  # one claim, whatever the language
OTHER = np.array([0.0, 1.0, 0.0])


def fake_embed(texts: Any) -> list[Any]:
    return [(CLAIM if "NATO" in t or "НАТО" in t or "Nato" in t else OTHER).astype(np.float16) for t in texts]


def post(n: int, text: str, lang: str, minute: int) -> Event:
    return Event(
        id=f"at://did:plc:a{n}/app.bsky.feed.post/{n}", platform=Platform.BLUESKY, kind=EventKind.POST,
        author=f"did:plc:a{n}", time_us=minute * 60_000_000, created_at="2026-11-14T10:00:00Z",
        text=text, langs=(lang,),
    )  # fmt: skip


def test_the_same_claim_in_three_languages_becomes_a_candidate() -> None:
    detector = SwarmDetector(embedder=fake_embed)
    texts = [
        ("NATO is moving troops to the Finnish border tonight, sources say", "en"),
        ("НАТО перебрасывает войска к финской границе этой ночью, говорят источники", "ru"),
        ("Nato siirtää joukkoja Suomen rajalle tänä yönä, kertovat lähteet sodasta", "fi"),
        ("NATO is moving troops to the Finnish border tonight, everyone says so", "en"),
    ]
    snapshots = []
    for n, (text, lang) in enumerate(texts):
        snapshots += detector.add(post(n, text, lang, minute=n))
    snapshots += detector.add(post(99, "Lovely weather in Espoo today, perfect for a walk", "en", minute=5))
    found = [s for s in snapshots if s.kind is GroupKind.TRANSLATED]
    assert found and found[-1].accounts == 4 and found[-1].stats["languages"] == 3


def test_one_language_or_unrelated_posts_do_not_pass() -> None:
    detector = SwarmDetector(embedder=fake_embed)
    snapshots = []
    for n in range(4):
        snapshots += detector.add(
            post(n, f"NATO troops at the border again today, report number {n}", "en", n)
        )
    assert not [s for s in snapshots if s.kind is GroupKind.TRANSLATED]  # 4 accounts, but 1 language


def test_refused_batches_are_split_until_the_flagged_text_is_found() -> None:
    calls: list[int] = []

    def nvidia(request: httpx.Request) -> httpx.Response:
        texts = __import__("json").loads(request.content)["input"]
        calls.append(len(texts))
        if any("flagged" in t for t in texts):
            return httpx.Response(
                422, json={"error": {"message": "input rejected by content safety screening"}}
            )
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [1.0, 0.0]} for i in range(len(texts))]}
        )

    embedder = NvidiaEmbedder("https://x", "k", "m", Budget(100, 1000), transport=httpx.MockTransport(nvidia))
    vectors = embedder(["a", "b", "flagged", "d"])
    assert [v is None for v in vectors] == [False, False, True, False]
    assert embedder.refused == 1 and calls[0] == 4


def test_regional_variants_count_as_one_language() -> None:
    detector = SwarmDetector(embedder=fake_embed)
    snapshots = []
    for n, lang in enumerate(["fr", "fr-FR", "fr-CA", "fr"]):
        snapshots += detector.add(
            post(n, f"NATO is moving troops to the border tonight, number {n}", lang, n)
        )
    assert not [s for s in snapshots if s.kind is GroupKind.TRANSLATED]  # French only
    assert detector.memory_summary()["translated_links"] >= 3


def test_the_detector_rebuilds_its_last_hour_on_start() -> None:
    from types import SimpleNamespace

    from kafka import TopicPartition

    from factslens.detector.__main__ import WarmStart

    class Consumer:
        def __init__(self) -> None:
            self.asked: dict[Any, int] = {}
            self.sought: list[tuple[Any, int]] = []

        def offsets_for_times(self, query: dict[Any, int]) -> dict[Any, Any]:
            self.asked = query
            return {tp: SimpleNamespace(offset=1234) for tp in query}

        def seek(self, tp: Any, offset: int) -> None:
            self.sought.append((tp, offset))

    consumer = Consumer()
    warm = WarmStart(consumer, minutes=60, clock=lambda: 10_000.0)
    tp = TopicPartition("posts", 0)
    warm.on_partitions_assigned({tp})
    assert consumer.asked == {} and consumer.sought == []  # nothing inside the client's poll
    warm.apply()
    warm.on_partitions_assigned({tp})  # a later rebalance: no second rewind
    warm.apply()
    assert consumer.asked == {tp: (10_000 - 3600) * 1000} and consumer.sought == [(tp, 1234)]
