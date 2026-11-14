"""Text embeddings from NVIDIA's hosted Nemotron Embed (NVIDIA Build, OpenAI-compatible).

Used to find the same claim posted in several languages (factslens.detector.translated). Calls are
batched (up to 64 texts), kept within a per-minute budget, and retried on rate limits. NVIDIA
screens inputs for safety and refuses a whole batch when one text is flagged (about 0.1% of posts
on our topics, measured 14 Nov), so a refused batch is split until the flagged texts are found;
those get no vector.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence

import httpx
import numpy as np

from factslens.common.settings import Settings
from factslens.investigator.llm import Budget, BudgetExhausted

log = logging.getLogger(__name__)

BATCH = 64
Vector = np.ndarray


class NvidiaEmbedder:
    def __init__(
        self,
        base_url: str,
        key: str,
        model: str,
        budget: Budget | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.budget = budget or Budget(per_minute=30, per_hour=1200)
        self.calls = 0
        self.refused = 0
        self._key = key
        self._sleep = sleep
        self._http = httpx.Client(base_url=base_url, timeout=60, transport=transport)

    @classmethod
    def from_settings(cls, settings: Settings) -> NvidiaEmbedder | None:
        if settings.nvidia_api_keys is None:
            return None
        key = settings.nvidia_api_keys.get_secret_value().split(",")[0].strip()
        return cls(settings.nvidia_base_url, key, settings.embed_model)

    def __call__(self, texts: Sequence[str]) -> list[Vector | None]:
        out: list[Vector | None] = []
        for start in range(0, len(texts), BATCH):
            out += self._batch(list(texts[start : start + BATCH]))
        return out

    def _batch(self, texts: list[str]) -> list[Vector | None]:
        try:
            self.budget.acquire()
        except BudgetExhausted:
            return [None] * len(texts)
        for attempt in range(4):
            self.calls += 1
            try:
                response = self._http.post(
                    "/embeddings",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={"model": self.model, "input": texts, "input_type": "query", "truncate": "END"},
                )
            except httpx.HTTPError as error:
                log.warning("embedding call failed", extra={"error": str(error)[:200]})
                self._sleep(2**attempt)
                continue
            if response.status_code == 200:
                rows = sorted(response.json()["data"], key=lambda d: d["index"])
                return [_unit(d["embedding"]) for d in rows]
            if response.status_code == 422 and "safety" in response.text:
                if len(texts) == 1:
                    self.refused += 1
                    return [None]
                half = len(texts) // 2
                return self._batch(texts[:half]) + self._batch(texts[half:])
            if response.status_code not in (429, 500, 502, 503, 504):
                log.warning(
                    "embedding refused", extra={"status": response.status_code, "body": response.text[:200]}
                )
                return [None] * len(texts)
            self._sleep(min(2 ** (attempt + 1), 20))
        return [None] * len(texts)


def _unit(values: list[float]) -> Vector:
    vector = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return (vector / norm if norm else vector).astype(np.float16)
