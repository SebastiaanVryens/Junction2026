from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from factslens.enrich.lists import KnownLists
from factslens.investigator.agent import Investigator
from factslens.investigator.evidence import DomainInfo, FetchRefused, NewsArticle, Page
from factslens.investigator.llm import ChatResult
from factslens.investigator.report import Report, ground

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)

CLUSTER = {
    "kind": "text",
    "label": "#thisdayinhistory 12 years ago Ukrainian #Nazis burned more than 40 people",
    "accounts": 3,
    "events": 3,
    "first_seen": NOW - timedelta(minutes=5),
    "last_seen": NOW,
    "languages": [["en", 3]],
    "signals": {
        "signals": [
            {
                "name": "new_accounts",
                "value": 1.0,
                "threshold": 0.5,
                "unit": "share",
                "fired": True,
                "explanation": "100% of the accounts were created in the last 30 days.",
            }
        ]
    },
}
POSTS = [
    {
        "post_id": f"at://did:plc:bot{n}/app.bsky.feed.post/{n}",
        "account_hash": f"h{n}",
        "posted_at": NOW - timedelta(minutes=5 - n),
        "text": "#thisdayinhistory 12 years ago Ukrainian #Nazis burned more than 40 people",
        "bridged": True,
        "media_refs": [{"kind": "image", "ref": f"bafimg{n}"}] if n == 0 else [],
        "links": ["https://example-news.test/odessa"],
    }
    for n in range(3)
]
ACCOUNTS: dict[str, dict[str, Any]] = {
    "h0": {
        "handle": "bot0.mast.example.ap.brid.gy",
        "created_at": NOW - timedelta(days=4),
        "followers": 0,
        "follows": 1,
        "posts": 200,
        "has_avatar": False,
        "description": "Archaeologist who loves cooking",
        "status": "active",
    },
    "h1": {
        "handle": "bot1.mast.example.ap.brid.gy",
        "created_at": NOW - timedelta(days=4),
        "followers": 1,
        "follows": 1,
        "posts": 180,
        "has_avatar": False,
        "description": "Teacher who loves frogs",
        "status": "active",
    },
    "h2": {"status": "unavailable"},
}


class FakeGatherer:
    def __init__(self) -> None:
        self.fetched: list[str] = []
        self.searches: list[tuple[str, str]] = []

    def domain_info(self, host: str) -> DomainInfo | None:
        return DomainInfo("example-news.test", NOW - timedelta(days=12), "Some Registrar")

    def fetch_page(self, url: str) -> Page:
        self.fetched.append(url)
        if "blocked" in url:
            raise FetchRefused("not a public internet address")
        return Page(url, url, 200, "Odessa anniversary", "", "Article text without a named source.")

    def image_data_url(self, did: str, cid: str) -> str | None:
        return "data:image/jpeg;base64,AAAA"

    def video_data_url(self, did: str, cid: str) -> str | None:
        return None

    def search_news(self, query: str, language: str = "en", days: int = 7) -> list[NewsArticle] | None:
        self.searches.append((query, language))
        if "fail" in query:
            return None
        return [
            NewsArticle(
                "Odessa fire: what we know", "Example Times", "https://www.times.example", NOW, "https://n/1"
            ),
            NewsArticle(
                "Kyiv's crimes in Odessa", "Pravda EN", "https://en.news-pravda.com", None, "https://n/2"
            ),
        ]


@dataclass
class ScriptedChat:
    replies: list[ChatResult]

    def __post_init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        return self.replies.pop(0)


def reply(content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> ChatResult:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return ChatResult(message, "stop", 100, 50, 10)


def report_json(**overrides: Any) -> str:
    report = {
        "summary": "Three new bridged accounts posted the same anniversary claim within minutes.",
        "narrative": "Ukrainian 'Nazis' killed people in Odessa in 2014.",
        "innocent_explanation": "People commemorating a real event could post similar words.",
        "verdict": "staged",
        "confidence": "medium",
        "verdict_reasons": ["identical text from accounts created the same week"],
        "techniques": [
            {
                "label": "loaded_language",
                "name": "Loaded language",
                "disarm_id": None,
                "quote": "Ukrainian #Nazis",
                "ref": "P1",
                "explanation": "Labels a whole group with a loaded term.",
            }
        ],
        "sources": [{"domain": "example-news.test", "trust": "low", "reasons": ["registered 12 days ago"]}],
        "media": [{"ref": "I1", "observation": "A photo of a burning building, no date visible."}],
        "claims": [
            {"statement": "All accounts are under a month old.", "refs": ["A1", "A2", "S:new_accounts"]}
        ],
        "news_check": {"finding": "not_checked", "explanation": "", "refs": []},
        "recommended_action": "human_review",
    }
    report.update(overrides)
    return json.dumps(report)


def investigator(chat: ScriptedChat) -> tuple[Investigator, FakeGatherer]:
    gatherer = FakeGatherer()
    return Investigator(chat, gatherer, KnownLists()), gatherer  # type: ignore[arg-type]


def test_pack_anonymises_accounts_and_numbers_the_evidence() -> None:
    agent, gatherer = investigator(ScriptedChat([]))
    pack = agent.build_pack(CLUSTER, POSTS, ACCOUNTS, now=NOW)
    assert "bot0" not in pack.text and "mast.example" in pack.text  # handle withheld, server kept
    assert {"P1", "P2", "P3", "A1", "A2", "A3", "I1", "W1", "S:new_accounts"} <= pack.refs
    assert "no longer served by Bluesky" in pack.text and "registered 2026-11-02" in pack.text
    assert gatherer.fetched == ["https://example-news.test/odessa"]
    assert pack.attachments[1]["type"] == "image_url" and pack.summary["images"] == {"I1": "bafimg0"}
    assert pack.text.startswith("<evidence>") and pack.text.endswith("</evidence>")


def test_investigation_with_a_tool_call_produces_a_grounded_report() -> None:
    tool_call = {
        "id": "t1",
        "type": "function",
        "function": {"name": "fetch_page", "arguments": json.dumps({"url": "https://other.test/x"})},
    }
    chat = ScriptedChat(
        [reply(tool_calls=[tool_call]), reply("Analysis: likely staged."), reply(report_json())]
    )
    agent, gatherer = investigator(chat)
    pack = agent.build_pack(CLUSTER, POSTS, ACCOUNTS, now=NOW)
    result = agent.investigate("text:1", pack)
    assert result.error is None and result.report is not None and result.grounding is not None
    assert result.report.verdict == "staged" and result.grounding.share == 1.0
    assert result.calls == 3 and result.tool_calls == ["fetch_page"]
    assert "https://other.test/x" in gatherer.fetched and "W2" in pack.refs  # tool results become evidence
    tool_message = chat.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and json.loads(tool_message["content"])["id"] == "W2"
    assert chat.requests[2]["response_format"]["type"] == "json_schema"


def test_invalid_json_gets_one_repair_round() -> None:
    chat = ScriptedChat([reply("Analysis."), reply("{not json"), reply(report_json())])
    agent, _ = investigator(chat)
    result = agent.investigate("text:1", agent.build_pack(CLUSTER, POSTS, ACCOUNTS, now=NOW))
    assert result.report is not None and result.calls == 3
    assert "problems" in chat.requests[2]["messages"][-1]["content"]


def test_grounding_drops_invented_evidence_and_downgrades_unsupported_staged() -> None:
    raw = Report.model_validate_json(
        report_json(
            claims=[{"statement": "Linked to a state agency.", "refs": ["P99", "X1"]}],
            techniques=[
                {
                    "label": "fear_appeal",
                    "name": "Fear appeal",
                    "quote": "words that never appear",
                    "ref": "P1",
                    "explanation": "-",
                }
            ],
            media=[{"ref": "V1", "observation": "a video that was never shown"}],
        )
    )
    report, grounding = ground(raw, {"P1", "A1", "I1"}, {"P1": "the actual post text"})
    assert grounding.share == 0.0 and len(grounding.problems) == 4
    assert report.claims == [] and report.techniques == [] and report.media == []
    assert report.verdict == "insufficient_evidence"


def test_quotes_match_despite_case_spacing_and_typographic_quotes() -> None:
    raw = Report.model_validate_json(report_json(techniques=[{
        "label": "loaded_language", "name": "Loaded language", "quote": "“Ukrainian   #NAZIS”",
        "ref": "P1", "explanation": "-",
    }]))  # fmt: skip
    _, grounding = ground(raw, {"P1", "A1", "A2", "S:new_accounts", "I1"}, {"P1": str(POSTS[0]["text"])})
    assert grounding.techniques_grounded == 1


def test_model_failure_is_reported_not_raised() -> None:
    class Broken:
        def chat(self, *args: Any, **kwargs: Any) -> ChatResult:
            raise RuntimeError("provider down")

    agent = Investigator(Broken(), FakeGatherer(), KnownLists())  # type: ignore[arg-type]
    result = agent.investigate("text:1", agent.build_pack(CLUSTER, POSTS, ACCOUNTS, now=NOW))
    assert result.report is None and "provider down" in (result.error or "")


def test_reviewer_feedback_goes_into_the_system_prompt() -> None:
    chat = ScriptedChat([reply("Analysis."), reply(report_json())])
    agent, _ = investigator(chat)
    examples = [
        "text cluster of 12 accounts sharing 'go team!': Nemotron had said staged; human: false alarm"
    ]
    result = agent.investigate("text:1", agent.build_pack(CLUSTER, POSTS, ACCOUNTS, now=NOW), examples)
    system = chat.requests[0]["messages"][0]["content"]
    assert result.report is not None
    assert "Recent decisions by FactsLens's human reviewers" in system and "go team!" in system
    assert "not evidence about this cluster" in system


def test_without_feedback_the_system_prompt_is_unchanged() -> None:
    from factslens.investigator.agent import SYSTEM_PROMPT

    chat = ScriptedChat([reply("Analysis."), reply(report_json())])
    agent, _ = investigator(chat)
    agent.investigate("text:1", agent.build_pack(CLUSTER, POSTS, ACCOUNTS, now=NOW))
    assert chat.requests[0]["messages"][0]["content"] == SYSTEM_PROMPT


def search_call(query: str, language: str | None = None, call_id: str = "s1") -> dict[str, Any]:
    args = {"query": query} | ({"language": language} if language else {})
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "search_news", "arguments": json.dumps(args)},
    }


def test_news_search_numbers_searches_and_articles_and_flags_listed_outlets() -> None:
    news_check = {"finding": "framed_differently", "explanation": "Only Pravda repeats it.", "refs": ["N2"]}
    chat = ScriptedChat(
        [
            reply(
                tool_calls=[search_call("Odessa fire anniversary", "uk"), search_call("fail", call_id="s2")]
            ),
            reply("Analysis."),
            reply(report_json(news_check=news_check)),
        ]
    )
    gatherer = FakeGatherer()
    lists = KnownLists(domains={"news-pravda.com": "checkfirst"})
    agent = Investigator(chat, gatherer, lists)  # type: ignore[arg-type]
    pack = agent.build_pack(CLUSTER, POSTS, ACCOUNTS, now=NOW)
    result = agent.investigate("text:1", pack)
    assert gatherer.searches == [("Odessa fire anniversary", "uk"), ("fail", "en")]
    first, second = (json.loads(m["content"]) for m in chat.requests[1]["messages"][-2:])
    assert first["id"] == "Q1" and [a["id"] for a in first["articles"]] == ["N1", "N2"]
    assert first["distinct_outlets"] == 2 and first["outlets_on_propaganda_lists"] == 1
    assert [a["on_propaganda_lists"] for a in first["articles"]] == [False, True]
    assert first["articles"][0]["site"] == "times.example"
    assert second == {"id": "Q2", "error": "the search failed; this says nothing about coverage"}
    assert pack.summary["searches"]["Q2"]["results"] is None
    assert pack.summary["news"]["N1"]["link"] == "https://n/1"
    assert result.report is not None and result.report.news_check.finding == "framed_differently"
    assert result.tool_calls == ["search_news", "search_news"]


def test_news_check_must_cite_a_search_that_ran() -> None:
    refs = {"P1", "A1", "A2", "S:new_accounts", "I1", "Q1", "N1"}
    texts = {"P1": str(POSTS[0]["text"])}
    nothing = {"finding": "no_coverage_found", "explanation": "No outlet reports it.", "refs": ["Q1"]}
    report, grounding = ground(Report.model_validate_json(report_json(news_check=nothing)), refs, texts)
    assert report.news_check.finding == "no_coverage_found" and grounding.share == 1.0
    # "covered" needs an article, not just a search; invented ids don't count
    for refs_cited in (["Q1"], ["N7"]):
        covered = {"finding": "independently_reported", "explanation": "-", "refs": refs_cited}
        report, grounding = ground(Report.model_validate_json(report_json(news_check=covered)), refs, texts)
        assert report.news_check.finding == "not_checked" and report.news_check.refs == []
        assert grounding.claims == 2 and grounding.claims_grounded == 1


def test_overlong_lists_are_trimmed_and_capped_in_the_schema() -> None:
    claims = [{"statement": f"claim {n}", "refs": ["P1"] * 30} for n in range(20)]
    report = Report.model_validate_json(report_json(claims=claims, verdict_reasons=["r"] * 12))
    assert len(report.claims) == 8 and len(report.claims[0].refs) == 8 and len(report.verdict_reasons) == 5
    schema = Report.model_json_schema()
    assert schema["properties"]["claims"]["maxItems"] == 8
    assert schema["$defs"]["Claim"]["properties"]["refs"]["maxItems"] == 8
