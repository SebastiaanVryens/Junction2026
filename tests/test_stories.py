from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from factslens.investigator.__main__ import keyword_patterns
from factslens.investigator.llm import ChatResult
from factslens.investigator.stories import (
    Headline,
    StoryPick,
    StoryReport,
    build_story_pack,
    compare,
    fetch_state_media,
    ground,
    pick_stories,
)

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
TASS = "TASS (state media; unofficial mirror on pravda.me)"
RYBAR = "Telegram channel @rybar (republished by the Pravda network)"
HEADLINES = [
    Headline(TASS, "state_media", NOW, "Russian forces liberated Tersyanka", "https://pravda.me/@tass/1"),
    Headline(RYBAR, "telegram", NOW, "Tersyanka is ours", "https://t.me/rybar/9", ["https://cdn.t.me/a.jpg"]),
    Headline("RT (state media)", "state_media", NOW, "Weather in Moscow", "https://pravda.me/@rt/2"),
]


class ScriptedChat:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        self.requests.append({"messages": messages, **kwargs})
        return ChatResult({"role": "assistant", "content": self.replies.pop(0)}, "stop", 3000, 900, 10)


def test_state_media_mirror_posts_from_the_lookback_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/lookup"):
            return httpx.Response(200, json={"id": "42"})
        return httpx.Response(
            200,
            json=[
                {
                    "created_at": "2026-11-14T11:30:00Z",
                    "content": "<p>Лавров &amp;quot;провел&amp;quot;</p>",
                    "url": "u1",
                },
                {"created_at": "2026-11-14T06:00:00Z", "content": "<p>old</p>", "url": "u2"},
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    found = fetch_state_media(client, NOW)
    assert {h.text for h in found} == {'Лавров "провел"'} and len(found) == 4  # one fresh post per outlet
    assert found[0].kind == "state_media" and "unofficial mirror on pravda.me" in found[0].source


def test_pick_keeps_only_real_headline_ids() -> None:
    searches = [{"language": "uk", "query": "Терсянка"}, {"language": "en", "query": "Tersyanka"}]
    first = {"title": "Fighting near Tersyanka", "refs": ["H1", "H2", "H9"], "keywords": ["Tersyanka"]}
    invented = {"title": "Invented", "refs": ["H7"], "keywords": [], "searches": []}
    picks = json.dumps({"stories": [first | {"searches": searches}, invented]})
    chat = ScriptedChat(picks)
    result = pick_stories(chat, HEADLINES, ["Earlier story"])  # type: ignore[arg-type]
    assert [p.title for p in result] == ["Fighting near Tersyanka"] and result[0].refs == ["H1", "H2"]
    assert "Earlier story" in chat.requests[0]["messages"][1]["content"]
    assert pick_stories(chat, [], []) == []  # type: ignore[arg-type]  # nothing to pick: no call


PICK = StoryPick(title="Fighting near Tersyanka", refs=["H1", "H2"], keywords=["Tersyanka"], searches=[])
NEWS: list[dict[str, Any]] = [
    {"language": "uk", "query": "Терсянка", "outlet": "Ukrainska Pravda", "site": "pravda.com.ua",
     "title": "Defenders hold Tersyanka", "published": "2026-11-14 10:00", "link": "https://n/1"},
    {"language": "en", "query": "Tersyanka", "outlet": "Pravda EN", "site": "news-pravda.com",
     "title": "Tersyanka liberated", "published": None, "link": "https://n/2", "listed": True},
]  # fmt: skip
CLUSTERS: list[dict[str, Any]] = [
    {"id": "server:truthsocial.co.in", "kind": "server", "label": "truthsocial.co.in", "tier": "watch",
     "accounts": 14, "verdict": "staged", "samples": ["Терсянка освобождена"]},
]  # fmt: skip


def test_pack_numbers_every_source_and_flags_listed_outlets() -> None:
    pack = build_story_pack(PICK, HEADLINES, NEWS, CLUSTERS, [("I1", "image", "data:image/jpeg;base64,AA")])
    assert {"H1", "H2", "N1", "N2", "S1", "I1"} <= pack.refs and "H3" not in pack.refs
    assert "Pravda EN [ON A PROPAGANDA LIST]" in pack.text and "Nemotron verdict staged" in pack.text
    assert (
        pack.evidence["clusters"] == {"S1": "server:truthsocial.co.in"}
        and pack.attachments[1]["type"] == "image_url"
    )


REPORT = {
    "title": "Fighting near Tersyanka",
    "summary": "TASS says Russian forces took Tersyanka; Ukrainian outlets say it is held.",
    "versions": [{"who": "TASS (state media)", "says": "liberated", "refs": ["H1"]},
                 {"who": "Ukrainian outlets", "says": "held", "refs": ["N1"]}],
    "agreements": [{"statement": "There is fighting near Tersyanka", "refs": ["H1", "N1"]}],
    "contradictions": [{"about": "facts", "statement": "Who controls the village", "refs": ["H1,N1"]},
                       {"about": "numbers", "statement": "made up", "refs": ["N9"]}],
    "only_in_state_media": [{"statement": "Liberated", "refs": ["H1", "N2"]}],
    "media": [{"ref": "I1", "observation": "smoke over fields"}, {"ref": "V1", "observation": "not shown"}],
    "amplification": [{"ref": "S1", "version": "TASS version", "note": "14 new accounts"}],
    "status": "contested",
    "confidence": "medium",
}  # fmt: skip


def test_ground_splits_ids_drops_unknown_and_downgrades_unsupported_contested() -> None:
    pack = build_story_pack(PICK, HEADLINES, NEWS, CLUSTERS, [("I1", "image", "data:,")])
    report, dropped = ground(StoryReport.model_validate(REPORT), pack.refs)
    assert report.contradictions[0].refs == ["H1", "N1"] and len(report.contradictions) == 1
    assert [m.ref for m in report.media] == ["I1"] and "media: V1" in dropped and report.status == "contested"
    bare = StoryReport.model_validate(
        REPORT | {"contradictions": [{"about": "facts", "statement": "x", "refs": ["N9"]}]}
    )
    report, dropped = ground(bare, pack.refs)
    assert report.status == "insufficient_coverage" and dropped[-1].startswith("status contested")


def test_compare_sends_media_and_repairs_once() -> None:
    chat = ScriptedChat("{nope", json.dumps(REPORT))
    pack = build_story_pack(PICK, HEADLINES, NEWS, CLUSTERS, [("I1", "image", "data:image/jpeg;base64,AA")])
    result = compare(chat, "Fighting near Tersyanka", pack)  # type: ignore[arg-type]
    assert result.report is not None and result.calls == 2 and result.error is None
    first = chat.requests[0]
    assert first["purpose"] == "story:compare" and first["messages"][1]["content"][2]["type"] == "image_url"


def test_keyword_patterns_skip_short_words_and_escape() -> None:
    assert keyword_patterns(["EU", "Терсянка", " Tersyanka ", "50%_off"]) == [
        "%Терсянка%",
        "%Tersyanka%",
        "%50off%",
    ]


def test_old_headline_window() -> None:
    assert NOW - timedelta(hours=1) > NOW - timedelta(hours=3)
