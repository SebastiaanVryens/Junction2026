from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from factslens.investigator.__main__ import needs_report
from factslens.investigator.campaigns import (
    CampaignReport,
    build_pack,
    campaign_key,
    find_campaigns,
    ground,
    investigate_campaign,
)
from factslens.investigator.llm import ChatResult

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def test_campaigns_are_reported_when_new_grown_or_failed_long_ago() -> None:
    assert needs_report(None, 3)
    assert not needs_report({"status": "done", "clusters": 10, "stale": True}, 14)
    assert needs_report({"status": "done", "clusters": 10, "stale": False}, 15)
    assert needs_report({"status": "failed", "clusters": 10, "stale": True}, 10)
    assert not needs_report({"status": "failed", "clusters": 10, "stale": False}, 10)


def test_find_campaigns_joins_clusters_sharing_two_accounts() -> None:
    scores = {"a": 90.0, "b": 60.0, "c": 50.0, "d": 80.0, "e": 10.0}
    links = [("a", "b", 3), ("b", "c", 2), ("d", "e", 1), ("x", "a", 5)]  # d-e: one shared account only
    assert find_campaigns(scores, links) == [["a", "b", "c"]]
    assert (
        campaign_key(["a", "b"]) == campaign_key(["a", "c"]) != campaign_key(["b"])
    )  # named by its top cluster


def cluster(n: int, label: str, score: float) -> dict[str, Any]:
    return {
        "id": f"text:{n}",
        "kind": "text",
        "label": label,
        "accounts": 3,
        "events": 3,
        "first_seen": NOW - timedelta(hours=2, minutes=n),
        "last_seen": NOW,
        "tier": "suspicious",
        "score": score,
        "signals": {
            "signals": [{"name": "copy_paste", "fired": True}, {"name": "new_accounts", "fired": False}]
        },
    }


CLUSTERS = [cluster(1, "Hi Anna, please support our fundraiser", 70), cluster(2, "Hi Ben, please help", 60)]
PROFILE = {
    "created_at": NOW - timedelta(days=3),
    "followers": 0,
    "follows": 1,
    "posts": 40,
    "description": "mum of two",
    "status": "active",
    "on_public_list": False,
}
MEMBERS = [
    {"cluster_id": f"text:{c}", "account_hash": h, "handle": f"{h}.bsky.social"} | PROFILE
    for c, h in ((1, "h1"), (1, "h2"), (2, "h1"), (2, "h2"), (2, "h3"))
]
POSTS = [
    {
        "cluster_id": "text:1",
        "account_hash": "h1",
        "posted_at": NOW - timedelta(hours=1),
        "text": "Hi Anna, please",
    },
    {
        "cluster_id": "text:2",
        "account_hash": "h3",
        "posted_at": NOW - timedelta(minutes=30),
        "text": "Hi Ben",
    },
]


def test_pack_lists_clusters_accounts_with_their_clusters_and_posts() -> None:
    findings = {"text:1": {"verdict": "staged", "confidence": "medium", "narrative": "begging for donations"}}
    pack = build_pack(CLUSTERS, MEMBERS, POSTS, findings, now=NOW)
    assert pack.clusters == {"C1": "text:1", "C2": "text:2"}
    assert pack.accounts["A1"] in {"h1", "h2"} and pack.accounts["A3"] == "h3"  # most clusters first
    assert (
        "A1: in C1, C2; Bluesky account" in pack.text and "h1.bsky.social" not in pack.text
    )  # names withheld
    assert "earlier single-cluster finding: staged" in pack.text and "signals: copy_paste;" in pack.text
    assert "P1 [C1, A1" in pack.text or "P1 [C1, A2" in pack.text
    assert {"C1", "C2", "A1", "A2", "A3", "P1", "P2"} <= pack.refs


REPORT = {
    "title": "Personalised donation-begging network",
    "summary": "Three new accounts send templated donation requests.",
    "relation": "one_campaign",
    "relation_reasons": ["the same accounts appear in both clusters"],
    "kind": "spam",
    "innocent_explanation": "Families genuinely asking for help.",
    "verdict": "staged",
    "confidence": "medium",
    "narratives": [{"narrative": "donation requests", "refs": ["C1,C9"]}],
    "operation": [
        {"statement": "Same template, names swapped", "refs": ["P1", "P2"]},
        {"statement": "x", "refs": ["Z1"]},
    ],
    "review_first": [{"ref": "A1", "reason": "in every cluster"}, {"ref": "A9", "reason": "?"}],
    "recommended_action": "human_review",
}  # fmt: skip


def test_ground_drops_unknown_ids_and_downgrades_unsupported_staged() -> None:
    pack = build_pack(CLUSTERS, MEMBERS, POSTS, {}, now=NOW)
    report, dropped = ground(CampaignReport.model_validate(REPORT), pack)
    assert report.narratives[0].refs == ["C1"] and len(report.operation) == 1
    assert [p.ref for p in report.review_first] == ["A1"] and report.verdict == "staged"
    assert "narrative: C9" in dropped and "review_first: A9" in dropped
    bare = CampaignReport.model_validate(REPORT | {"operation": [{"statement": "x", "refs": ["Q1"]}]})
    report, dropped = ground(bare, pack)
    assert report.verdict == "insufficient_evidence" and dropped[-1].startswith("verdict staged")


def test_investigate_campaign_repairs_once_then_grounds() -> None:
    class Chat:
        def __init__(self) -> None:
            self.replies = ["{broken", json.dumps(REPORT)]
            self.kwargs: list[dict[str, Any]] = []

        def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
            self.kwargs.append(kwargs)
            return ChatResult({"role": "assistant", "content": self.replies.pop(0)}, "stop", 20000, 900, 10)

    chat = Chat()
    pack = build_pack(CLUSTERS, MEMBERS, POSTS, {}, now=NOW)
    result = investigate_campaign(chat, "campaign:x", pack)  # type: ignore[arg-type]
    assert result.report is not None and result.calls == 2 and result.prompt_tokens == 40000
    assert (
        chat.kwargs[0]["purpose"] == "campaign:report"
        and chat.kwargs[0]["response_format"]["type"] == "json_schema"
    )
    assert result.error is None and "review_first: A9" in result.dropped
