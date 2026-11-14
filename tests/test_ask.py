"""Ask the network: read-only tools with short ids, grounding of citations, the agent loop."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from test_investigator import ScriptedChat, reply

from factslens.enrich.lists import KnownLists
from factslens.investigator import ask

GROUP = {
    "id": "text:abc",
    "kind": "text",
    "label": "NATO is preparing to attack Finland",
    "tier": "suspicious",
    "score": 0.8,
    "accounts": 14,
    "verdict": "staged",
    "summary": "Fourteen new accounts posted the same claim.",
}


class FakeData:
    def __init__(self) -> None:
        self.asked: list[tuple[Any, ...]] = []

    async def ask_overview(self, hours: int) -> dict[str, Any]:
        return {"groups_by_tier": {"suspicious": {"groups": 3, "accounts": 40}}, "campaigns": 1, "stories": 2}

    async def ask_groups(
        self, patterns: list[str], tier: str | None, platform: str | None, hours: int
    ) -> list[dict[str, Any]]:
        self.asked.append((patterns, tier, platform, hours))
        return [GROUP] if not patterns or "%nato%" in [p.lower() for p in patterns] else []

    async def ask_group(self, cluster_id: str) -> dict[str, Any] | None:
        if cluster_id != "text:abc":
            return None
        signals = {
            "signals": [
                {"name": "synchrony", "fired": True, "explanation": "60% within one minute."},
                {"name": "copy_paste", "fired": False, "explanation": "0% copied."},
            ]
        }
        return GROUP | {
            "signals": signals,
            "first_seen": "2026-11-14 10:00",
            "last_seen": "2026-11-14 11:00",
            "narrative": "NATO plans an attack.",
            "techniques": ["Fear appeal"],
            "sample_posts": ["NATO is preparing to attack Finland!"],
            "campaign": {"id": 7, "title": "Finland fear push"},
            "stories": [{"id": 3, "title": "NATO exercise"}],
        }

    async def campaigns(self, hours: int) -> list[dict[str, Any]]:
        return [
            {
                "id": 7,
                "title": "Finland fear push",
                "kind": "propaganda",
                "verdict": "staged",
                "cluster_ids": ["text:abc", "link:x"],
                "accounts": 20,
                "report": {"summary": "Two groups."},
            }
        ]

    async def stories(self, hours: int) -> list[dict[str, Any]]:
        return [
            {
                "id": 3,
                "title": "NATO exercise in Finland",
                "story_status": "contested",
                "report": {"summary": "x"},
            },
            {"id": 4, "title": "Bulgaria budget", "story_status": "consistent", "report": {"summary": "y"}},
        ]

    async def ask_domains(self, hours: int) -> list[dict[str, Any]]:
        return [
            {"domain": "youtube.com", "groups": 9, "accounts": 50},
            {"domain": "news-pravda.com", "groups": 3, "accounts": 12},
        ]

    async def ask_members(self, hours: int) -> list[dict[str, Any]]:
        return [
            {"cluster_id": "a", "account_hash": "h1", "did": "telegram:rybar", "handle": None},
            {"cluster_id": "b", "account_hash": "h1", "did": "telegram:rybar", "handle": None},
            {
                "cluster_id": "a",
                "account_hash": "h2",
                "did": "did:plc:x",
                "handle": "u.bad.example.ap.brid.gy",
            },
            {
                "cluster_id": "c",
                "account_hash": "h3",
                "did": "mastodon:u@bad.example",
                "handle": "u@bad.example",
            },
            {"cluster_id": "c", "account_hash": "h4", "did": "did:plc:y", "handle": "someone.bsky.social"},
        ]


def tools() -> ask.Tools:
    lists = KnownLists(
        domains={"news-pravda.com": "checkfirst"}, pravda_channels={"rybar"}, servers={"bad.example"}
    )
    return ask.Tools(FakeData(), lists)  # type: ignore[arg-type]


def run(tool: ask.Tools, name: str, **args: Any) -> dict[str, Any]:
    return asyncio.run(tool.run(name, json.dumps(args)))


def test_groups_get_short_ids_and_details_only_by_those_ids() -> None:
    t = tools()
    found = run(t, "search_groups", words=["NATO", "50%_off"], tier="suspicious", platform="x")
    assert found["items"][0]["id"] == "G1" and found["items"][0]["nemotron_verdict"] == "staged"
    assert t.data.asked[0] == (["%NATO%", "%50off%"], "suspicious", None, 72)  # type: ignore[attr-defined]
    details = run(t, "group_details", id="G1")
    assert details["signals_fired"] == ["60% within one minute."]
    assert details["campaign"] == "K1" and details["stories"] == ["S1"]
    assert t.registry.items["K1"]["url"] == "/campaigns/7"
    assert "error" in run(t, "group_details", id="text:abc")  # raw ids are not accepted
    assert "error" in run(t, "group_details", id="G9")
    assert "account" not in json.dumps(details).replace("accounts", "")  # no account names or ids


def test_websites_skip_platforms_and_sources_are_counted_not_named() -> None:
    t = tools()
    sites = run(t, "top_websites")["items"]
    assert [s["website"] for s in sites] == ["news-pravda.com"] and sites[0]["on_propaganda_lists"]
    sources = {s["source"]: s for s in run(t, "top_sources")["items"]}
    assert sources["Telegram channel @rybar"]["flagged_groups"] == 2
    assert sources["Telegram channel @rybar"]["on_iftas_or_pravda_lists"]
    assert sources["Mastodon server bad.example, bridged to Bluesky"]["on_iftas_or_pravda_lists"]
    assert sources["Mastodon server bad.example"]["accounts"] == 1
    assert sources["Bluesky (native accounts)"]["accounts"] == 1
    assert t.registry.items["O1"]["url"] == "https://t.me/s/rybar"


def test_stories_filter_by_words_and_campaigns_list() -> None:
    t = tools()
    assert [s["title"] for s in run(t, "list_stories", words=["finland"])["items"]] == [
        "NATO exercise in Finland"
    ]
    assert run(t, "list_campaigns")["items"][0]["groups"] == 2
    assert run(t, "nonsense")["error"].startswith("unknown tool")


def answer_json(**overrides: Any) -> str:
    answer = {
        "status": "answered",
        "answer": "Yes: one suspicious group pushes a claim that NATO will attack Finland [G1], part of a "
        "campaign [K1, K9].",
        "points": [
            {"statement": "G1 — Fourteen accounts, Nemotron judged it staged [G1].", "refs": ["G1"]},
            {"statement": "An invented point.", "refs": ["G7"]},
        ],
        "caveats": ["Only the last 72 hours."],
        "confidence": "medium",
    }
    return json.dumps(answer | overrides)


def test_the_agent_looks_then_answers_with_grounded_citations() -> None:
    chat = ScriptedChat(
        [
            reply(
                tool_calls=[
                    {
                        "id": "1",
                        "type": "function",
                        "function": {"name": "search_groups", "arguments": '{"words": ["nato"]}'},
                    }
                ]
            ),
            reply(
                tool_calls=[
                    {
                        "id": "2",
                        "type": "function",
                        "function": {"name": "group_details", "arguments": '{"id": "G1"}'},
                    }
                ]
            ),
            reply("One suspicious group, part of a campaign."),
            reply(answer_json()),
        ]
    )
    steps: list[str] = []
    result = asyncio.run(
        ask.ask(chat, tools(), "Is anyone pushing NATO stories about Finland?", steps.append)
    )  # type: ignore[arg-type]
    assert result.error is None and result.answer is not None and result.calls == 4
    assert steps == ["searched groups for “nato” (1 found)", "opened group G1"]
    assert result.answer.answer.endswith("part of a campaign [K1].")  # K9 was never returned
    assert [p.refs for p in result.answer.points] == [["G1"]]  # the point citing G7 is gone
    assert result.answer.points[0].statement == "Fourteen accounts, Nemotron judged it staged."
    assert "K9" in result.dropped and "G7" in result.dropped
    first = chat.requests[0]["messages"][1]["content"]
    assert "groups_by_tier" in first and "Question: Is anyone pushing NATO" in first


def test_a_failing_model_is_reported_not_raised() -> None:
    class Broken:
        def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
            raise RuntimeError("NVIDIA is down")

    result = asyncio.run(ask.ask(Broken(), tools(), "anything"))  # type: ignore[arg-type]
    assert result.answer is None and result.error and "NVIDIA is down" in result.error


class FakeNews:
    def __init__(self) -> None:
        self.searched: list[tuple[str, str]] = []

    def search_news(self, query: str, language: str = "en", days: int = 7) -> Any:
        from factslens.investigator.evidence import NewsArticle

        self.searched.append((query, language))
        if "fail" in query:
            return None
        return [
            NewsArticle("Talks resume in Geneva", "Reuters", "https://www.reuters.com", None, "https://n/1"),
            NewsArticle("Kyiv surrenders", "Pravda EN", "https://en.news-pravda.com", None, "https://n/2"),
        ]


def test_news_questions_get_independent_articles_with_ids_and_links() -> None:
    t = tools()
    t.news = FakeNews()
    found = run(t, "search_news", query="Ukraine peace talks", language="uk")
    assert [i["id"] for i in found["items"]] == ["N1", "N2"] and found["distinct_outlets"] == 2
    assert [i["on_propaganda_lists"] for i in found["items"]] == [False, True]  # Pravda is listed
    assert t.registry.items["N1"]["url"] == "https://n/1"
    assert t.news.searched == [("Ukraine peace talks", "uk")]
    assert "failed" in run(t, "search_news", query="fail")["error"]
    assert run(t, "search_news", query="x", language="xx")["edition"] == "en"
    assert ask.describe(t.used[0]) == "searched independent news for “Ukraine peace talks” (uk) (2 found)"
