"""The same claim in several languages: posts on our topics, embedded with Nemotron Embed.

Our text matching sees identical wording only. A network that translates one claim into many
languages (the Pravda network's pattern) looks unrelated to it. Here posts on our topics are
embedded in batches; a post links to the most similar recent post (cosine >= THRESHOLD) from another
account within the hour. Linked posts form a TRANSLATED group, which becomes a candidate when it has
3+ accounts in 2+ languages (factslens.detector.groups); scoring and Nemotron then judge it like
any other group.

Measured on 3 recorded hours (14 Nov, 7,704 posts on our topics): at 0.8, 20 cross-language groups,
10 of them with 3+ accounts; unrelated posts score about 0.05, the same claim across languages
0.68-0.83.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from factslens.common.topics import on_topic

THRESHOLD = 0.8
WINDOW_US = 60 * 60_000_000
FLUSH_AFTER_US = 60_000_000  # embed at least once a minute (stream time)
BATCH = 64
MAX_RECENT = 8000  # about 3 hours at night, 1 hour at peak; float16 vectors: ~33 MB
MIN_CHARS = 40

Embedder = Callable[[Sequence[str]], list[Any]]


def worth_embedding(text: str) -> bool:
    return len(text) >= MIN_CHARS and on_topic(text)


class TranslatedIndex:
    """Feed `add` in stream order; `flush` embeds the pending posts and returns new links."""

    def __init__(self, embed: Embedder) -> None:
        self.embed = embed
        self.pending: list[Any] = []  # items waiting for a vector
        self.recent: deque[tuple[Any, np.ndarray]] = deque()
        self.links = 0

    def add(self, item: Any) -> None:
        self.pending.append(item)

    def due(self, now_us: int) -> bool:
        return bool(self.pending) and (
            len(self.pending) >= BATCH or now_us - self.pending[0].time_us >= FLUSH_AFTER_US
        )

    def flush(self, now_us: int) -> list[tuple[Any, Any]]:
        """(new item, most similar earlier item from another account) for every new link."""
        items, self.pending = self.pending, []
        vectors = self.embed([i.text for i in items])
        while self.recent and (
            len(self.recent) > MAX_RECENT or now_us - self.recent[0][0].time_us > WINDOW_US
        ):
            self.recent.popleft()
        valid = [(i, v) for i, v in zip(items, vectors, strict=True) if v is not None]
        if not valid:
            return []
        known = [i for i, _ in self.recent] + [i for i, _ in valid]
        matrix = np.vstack([v for _, v in self.recent] + [v for _, v in valid]).astype(np.float32)
        new = matrix[len(self.recent) :]
        sims = new @ matrix.T  # one multiplication per batch: new posts against everything recent
        links: list[tuple[Any, Any]] = []
        for b, (item, _) in enumerate(valid):
            earlier = len(self.recent) + b  # only posts that came before this one
            row = sims[b, :earlier]
            best, best_sim = None, THRESHOLD
            for index in np.nonzero(row >= THRESHOLD)[0]:
                other = known[int(index)]
                near = abs(other.time_us - item.time_us) <= WINDOW_US
                if other.author != item.author and near and row[index] >= best_sim:
                    best, best_sim = other, float(row[index])
            if best is not None:
                links.append((item, best))
        self.recent.extend(valid)
        self.links += len(links)
        return links
