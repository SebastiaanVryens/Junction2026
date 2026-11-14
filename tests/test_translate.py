from __future__ import annotations

import json
from typing import Any

from factslens.investigator.llm import ChatResult
from factslens.investigator.translate import MAX_ITEMS, translate


class FakeChat:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        self.requests.append({"messages": messages, **kwargs})
        return ChatResult({"role": "assistant", "content": self.reply}, "stop", 100, 50, 10)


def reply(*items: tuple[str, str, str]) -> str:
    return json.dumps({"items": [{"id": i, "language": lang, "english": text} for i, lang, text in items]})


def test_translate_maps_answers_back_to_keys() -> None:
    chat = FakeChat(
        reply(("T1", "ES", "The Amazon is burning"), ("T2", "en", "already English"), ("T9", "x", "?"))
    )
    result = translate(
        chat, {"post:a": "La Amazonía arde", "post:b": "already English", "post:c": "  "}, "c1"
    )
    assert result == {"post:a": ("es", "The Amazon is burning"), "post:b": ("en", "already English")}
    request = chat.requests[0]
    assert request["purpose"] == "translate" and request["cluster_id"] == "c1"
    assert request["response_format"]["type"] == "json_schema"
    lines = request["messages"][1]["content"].splitlines()[1:]
    assert [json.loads(line)["id"] for line in lines] == ["T1", "T2"]  # the empty post is not sent
    assert "do not soften" in request["messages"][0]["content"]


def test_translate_is_bounded_and_tolerates_bad_output() -> None:
    chat = FakeChat("not json")
    texts = {f"post:{n}": "x" * 5000 for n in range(MAX_ITEMS + 10)}
    assert translate(chat, texts) == {}
    lines = chat.requests[0]["messages"][1]["content"].splitlines()[1:]
    assert len(lines) == MAX_ITEMS and len(json.loads(lines[0])["text"]) == 800
    assert translate(FakeChat("{}"), {}) == {}  # nothing to translate: no call
