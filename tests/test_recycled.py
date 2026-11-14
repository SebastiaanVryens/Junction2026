from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from PIL import Image, ImageDraw

from factslens.investigator.__main__ import source_label
from factslens.investigator.llm import ChatResult
from factslens.investigator.recycled import (
    SAME_IMAGE_DISTANCE,
    Sighting,
    dhash,
    distance,
    is_reuse,
    judge,
)

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def picture(size: int, variant: int = 0, quality: int = 90) -> bytes:
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    if variant == 0:
        draw.rectangle((size * 0.1, size * 0.2, size * 0.6, size * 0.9), fill="black")
        draw.ellipse((size * 0.55, size * 0.05, size * 0.95, size * 0.45), fill="grey")
    else:
        draw.rectangle((size * 0.5, size * 0.1, size * 0.95, size * 0.4), fill="black")
        draw.ellipse((size * 0.05, size * 0.5, size * 0.45, size * 0.95), fill="grey")
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality)
    return out.getvalue()


def test_the_hash_survives_resizing_and_recompression_but_not_a_different_picture() -> None:
    original, small, other = (
        dhash(picture(600)),
        dhash(picture(200, quality=40)),
        dhash(picture(600, variant=1)),
    )
    assert original is not None and small is not None and other is not None
    assert distance(original, small) <= SAME_IMAGE_DISTANCE
    assert distance(original, other) > SAME_IMAGE_DISTANCE
    assert -(2**63) <= original < 2**63  # fits a Postgres bigint


def test_blank_images_get_no_hash_so_they_never_match() -> None:
    blank = io.BytesIO()
    Image.new("RGB", (300, 300), (250, 250, 250)).save(blank, "PNG")
    assert dhash(blank.getvalue()) is None
    assert dhash(picture(300)) is not None


def test_a_failing_periodic_round_does_not_stop_the_investigator() -> None:
    from factslens.investigator.__main__ import periodic

    def broken() -> int:
        raise RuntimeError("http_429")

    assert periodic("test", broken) == 0
    assert periodic("test", lambda: 3) == 3


def sighting(account: str, caption: str, minutes: int) -> Sighting:
    return Sighting(
        f"post-{account}", "c", account, NOW + timedelta(minutes=minutes), caption, "Bluesky account"
    )


def test_reuse_needs_another_account_and_a_different_caption() -> None:
    earlier = sighting("a", "Floods in Valencia, October 2024: streets under water", 0)
    assert is_reuse(earlier, sighting("b", "Kyiv this morning after the missile strike on the centre", 60), 2)
    assert not is_reuse(
        earlier, sighting("a", "Kyiv this morning after the missile strike", 60), 2
    )  # same account
    assert not is_reuse(
        earlier, sighting("b", "Floods in Valencia, October 2024: streets under water!", 60), 2
    )
    assert not is_reuse(
        earlier, sighting("b", "Kyiv this morning after the strike", 60), 12
    )  # different picture


def test_judge_sends_the_picture_and_both_captions() -> None:
    class Chat:
        model = "m"

        def __init__(self) -> None:
            self.request: dict[str, Any] = {}

        def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
            self.request = {"messages": messages, **kwargs}
            answer = {"observation": "flooded street", "clues": ["Spanish sign"], "supports": "earlier",
                      "explanation": "Spanish signs; no war damage."}  # fmt: skip
            return ChatResult({"role": "assistant", "content": json.dumps(answer)}, "stop", 900, 200, 10)

    chat = Chat()
    earlier = sighting("a", "Floods in Valencia", 0)
    later = sighting("b", "Kyiv after the strike", 60)
    verdict, _ = judge(chat, "data:image/jpeg;base64,AA", earlier, later)  # type: ignore[arg-type]
    assert verdict is not None and verdict.supports == "earlier"
    content = chat.request["messages"][1]["content"]
    assert "Floods in Valencia" in content[0]["text"] and "Kyiv after the strike" in content[0]["text"]
    assert content[1]["type"] == "image_url" and chat.request["purpose"] == "recycled:judge"


def test_source_labels() -> None:
    assert source_label("telegram:rybar") == "Telegram channel @rybar"
    assert source_label("hahiraz@truthsocial.co.in") == "Mastodon account on truthsocial.co.in"
    assert source_label("someone.bsky.social") == source_label(None) == "Bluesky account"
