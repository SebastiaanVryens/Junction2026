"""Chat client for Nemotron on any OpenAI-compatible provider, with a hard call budget.

Every call is retried on 429/5xx with exponential backoff, counted against a per-minute and
per-hour budget (so the free tier is never exhausted by surprise), and reported to a log
callback with latency, tokens, attempts and errors.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from factslens.common.settings import MissingSettingError, Settings

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class BudgetExhausted(RuntimeError):
    """The hourly call budget is used up; try again later."""


class ModelCallError(RuntimeError):
    pass


@dataclass(frozen=True)
class CallRecord:
    purpose: str
    cluster_id: str | None
    provider: str
    model: str
    status: str
    attempts: int
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ChatResult:
    message: dict[str, Any]
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int

    @property
    def content(self) -> str:
        return str(self.message.get("content") or "")

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        calls = self.message.get("tool_calls")
        return calls if isinstance(calls, list) else []


@dataclass
class Budget:
    """Sliding-window call limits. `acquire` waits for the minute window; it never waits for the
    hour window, it raises instead, so a long pause is a visible decision rather than a hang."""

    per_minute: int
    per_hour: int
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _calls: deque[float] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)  # several threads may call at once

    def used_last_hour(self) -> int:
        self._trim()
        return len(self._calls)

    def _trim(self) -> None:
        now = self.clock()
        while self._calls and now - self._calls[0] > 3600:
            self._calls.popleft()

    def acquire(self) -> None:
        while True:
            with self._lock:
                self._trim()
                if len(self._calls) >= self.per_hour:
                    raise BudgetExhausted(f"{self.per_hour} model calls in the last hour")
                now = self.clock()
                recent = [t for t in self._calls if now - t < 60]
                if len(recent) < self.per_minute:
                    self._calls.append(now)
                    return
                wait = 60 - (now - recent[0]) + 0.05
            self.sleep(wait)


CallLogger = Callable[[CallRecord], None]


class ChatClient:
    def __init__(
        self,
        base_url: str,
        keys: Sequence[str],
        model: str,
        budget: Budget,
        logger: CallLogger | None = None,
        *,
        provider: str = "nvidia-build",
        max_attempts: int = 6,  # backoff 2+4+8+16+30 s: rides out a one-minute rate window
        timeout_s: float = 180.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        gate: Callable[[], None] | None = None,
        max_parallel: int = 1,
    ) -> None:
        if not keys:
            raise ValueError("at least one API key is required")
        self.model = model
        self.provider = provider
        self.budget = budget
        self._keys = itertools.cycle(keys)
        self._logger = logger
        self._max_attempts = max_attempts
        self._sleep = sleep
        # NVIDIA accepts parallel requests on one key (measured 14 Nov); its limit is the request
        # rate, which the budget and the retries handle. Services are sequential (1); the website
        # answers several people at once.
        self._slots = threading.BoundedSemaphore(max_parallel)
        self.gate = gate  # called before every attempt; may wait (e.g. while people wait for an answer)
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        logger: CallLogger | None = None,
        max_attempts: int = 6,
        max_parallel: int = 1,
        per_hour: int | None = None,
    ) -> ChatClient:
        if settings.nvidia_api_keys is None:
            raise MissingSettingError("NVIDIA_API_KEYS")
        keys = [k.strip() for k in settings.nvidia_api_keys.get_secret_value().split(",") if k.strip()]
        budget = Budget(
            settings.nemotron_max_calls_per_minute, per_hour or settings.nemotron_max_calls_per_hour
        )
        return cls(
            settings.nvidia_base_url,
            keys[: max(1, settings.nemotron_keys_in_use)],
            settings.nemotron_model,
            budget,
            logger,
            max_attempts=max_attempts,
            max_parallel=max_parallel,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        purpose: str,
        cluster_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 2000,
        thinking_tokens: int | None = 1024,
        temperature: float = 0.2,
    ) -> ChatResult:
        self.budget.acquire()
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format
        if thinking_tokens is not None:
            body["nvext"] = {"max_thinking_tokens": thinking_tokens}

        started = time.perf_counter()
        status, error = "error", None
        response: httpx.Response | None = None
        attempts = 0
        with self._slots:
            for attempts in range(1, self._max_attempts + 1):
                if self.gate is not None:
                    self.gate()
                try:
                    response = self._http.post(
                        "/chat/completions",
                        json=body,
                        headers={"Authorization": f"Bearer {next(self._keys)}"},
                    )
                except httpx.HTTPError as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    response = None
                else:
                    if response.status_code == 200:
                        status = "ok"
                        break
                    status, error = f"http_{response.status_code}", response.text[:300]
                    if response.status_code not in RETRY_STATUSES:
                        break
                if attempts < self._max_attempts:
                    self._sleep(min(2**attempts, 30))
        latency_ms = int((time.perf_counter() - started) * 1000)

        usage: dict[str, Any] = {}
        if status == "ok" and response is not None:
            data = response.json()
            usage = data.get("usage") or {}
        record = CallRecord(
            purpose=purpose,
            cluster_id=cluster_id,
            provider=self.provider,
            model=self.model,
            status=status,
            attempts=attempts,
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            error=error if status != "ok" else None,
        )
        if self._logger is not None:
            try:
                self._logger(record)
            except Exception:  # logging must never break an investigation
                log.exception("could not log model call")
        if status != "ok" or response is None:
            raise ModelCallError(f"{purpose}: {status}: {error}")
        choice = data["choices"][0]
        return ChatResult(
            message=choice.get("message") or {},
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
        )
