from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from factslens.investigator.llm import (
    Budget,
    BudgetExhausted,
    CallRecord,
    ChatClient,
    ModelCallError,
)

OK = {
    "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
    "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "completion_tokens_details": {"reasoning_tokens": 3},
    },
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def client(
    responses: list[httpx.Response], records: list[CallRecord], keys: list[str] | None = None
) -> tuple[ChatClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(queue)

    budget = Budget(per_minute=100, per_hour=1000)
    chat = ChatClient(
        "https://llm.test/v1",
        keys or ["k1"],
        "nvidia/nemotron-3.5-super-vl-120b-a12b",
        budget,
        records.append,
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    return chat, seen


def test_successful_call_is_logged_with_tokens_and_sends_thinking_budget() -> None:
    records: list[CallRecord] = []
    chat, seen = client([httpx.Response(200, json=OK)], records)
    result = chat.chat(
        [{"role": "user", "content": "x"}], purpose="test", cluster_id="c1", thinking_tokens=256
    )
    assert result.content == "hi" and result.prompt_tokens == 10
    body = json.loads(seen[0].content)
    assert body["nvext"] == {"max_thinking_tokens": 256} and body["model"].endswith("super-vl-120b-a12b")
    assert seen[0].headers["authorization"] == "Bearer k1"
    assert records[0].status == "ok" and records[0].reasoning_tokens == 3 and records[0].cluster_id == "c1"


def test_retries_rate_limits_then_succeeds_and_rotates_keys() -> None:
    records: list[CallRecord] = []
    chat, seen = client(
        [httpx.Response(429, text="slow down"), httpx.Response(200, json=OK)], records, ["k1", "k2"]
    )
    chat.chat([{"role": "user", "content": "x"}], purpose="test")
    assert [r.headers["authorization"] for r in seen] == ["Bearer k1", "Bearer k2"]
    assert records[0].attempts == 2 and records[0].status == "ok"


def test_client_error_is_not_retried_and_raises() -> None:
    records: list[CallRecord] = []
    chat, seen = client([httpx.Response(400, text="bad schema")], records)
    with pytest.raises(ModelCallError):
        chat.chat([{"role": "user", "content": "x"}], purpose="test")
    assert len(seen) == 1 and records[0].status == "http_400" and "bad schema" in (records[0].error or "")


def test_tool_calls_are_exposed() -> None:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "fetch_page", "arguments": "{}"}}
        ],
    }
    chat, _ = client(
        [httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "tool_calls"}]})], []
    )
    result = chat.chat([{"role": "user", "content": "x"}], purpose="test", tools=[{"type": "function"}])
    assert result.tool_calls[0]["function"]["name"] == "fetch_page" and result.content == ""


def test_budget_waits_for_the_minute_window_and_stops_at_the_hour_cap() -> None:
    clock = FakeClock()
    budget = Budget(per_minute=2, per_hour=3, clock=clock, sleep=clock.sleep)
    budget.acquire()
    budget.acquire()
    budget.acquire()  # third call in the same minute has to wait
    assert clock.slept and 59 < clock.slept[0] <= 61
    with pytest.raises(BudgetExhausted):
        budget.acquire()
    assert budget.used_last_hour() == 3
    clock.now += 3601
    budget.acquire()  # an hour later the budget is free again
