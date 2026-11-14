from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi.testclient import TestClient

from factslens.api.app import create_app, post_url, sparkline
from factslens.common.db import ServiceStatus

NOW = datetime.now(UTC)
TOKEN = "s3cret-review-token"


def statuses(ingest_age_s: float = 2, detector_age_s: float = 3) -> dict[str, ServiceStatus]:
    return {
        "ingest": ServiceStatus(
            "ingest",
            NOW - timedelta(seconds=ingest_age_s),
            {
                "total": 2100,
                "uptime_seconds": 60,
                "last_time_us": int((time.time() - 4) * 1e6),
                "reconnects": 1,
            },
        ),
        "detector": ServiceStatus(
            "detector",
            NOW - timedelta(seconds=detector_age_s),
            {
                "consumer_lag": 12,
                "events_last_minute": {"post": 3000, "repost": 3600},
                "top_domains_10m": [["example.com", 42]],
                "top_hashtags_10m": [["breaking", 7]],
            },
        ),
        "enrich": ServiceStatus("enrich", NOW - timedelta(seconds=1), {"profiles_fetched": 5}),
        "telegram": ServiceStatus(
            "telegram",
            NOW - timedelta(seconds=3),
            {"total": 12, "polls": 40, "uptime_seconds": 120, "watched": 40},
        ),
        "mastodon": ServiceStatus(
            "mastodon",
            NOW - timedelta(seconds=4),
            {"total": 30, "polls": 59, "uptime_seconds": 60, "watched": 59},
        ),
        "investigator": ServiceStatus(
            "investigator",
            NOW - timedelta(seconds=2),
            {"investigations": 2, "calls_last_hour": 7, "budget_per_hour": 300},
        ),
    }


SIGNALS = {
    "signals": [
        {
            "name": "new_accounts",
            "category": "account",
            "value": 0.9,
            "threshold": 0.5,
            "fired": True,
            "weight": 20,
            "unit": "share",
            "explanation": "90% of the accounts were created in the last 30 days.",
        }
    ],
    "reasons": ["Coordinated activity by accounts with several signs of being inauthentic."],
    "profile_coverage": 1.0,
}
CLUSTER = {
    "id": "text:abc",
    "kind": "text",
    "label": "Same message from many new accounts",
    "first_seen": NOW - timedelta(minutes=30),
    "last_seen": NOW,
    "accounts": 6,
    "events": 7,
    "score": 55.0,
    "tier": "suspicious",
    "tier_or_pending": "suspicious",
    "signals": SIGNALS,
    "per_minute": [1, 0, 3, 2, 1],
    "languages": [["en", 7]],
    "domains": [],
}


REPORT = {
    "summary": "Six new accounts pushed the same claim within minutes.",
    "narrative": "A neutral description of the pushed story.",
    "innocent_explanation": "Friends sharing the same news story.",
    "verdict": "staged",
    "confidence": "medium",
    "verdict_reasons": ["identical text from week-old accounts"],
    "techniques": [
        {"label": "fear_appeal", "name": "Fear appeal", "disarm_id": None, "quote": "they are coming",
         "ref": "P1", "explanation": "Plays on fear."}
    ],
    "sources": [{"domain": "example.com", "trust": "low", "reasons": ["registered 12 days ago"]}],
    "media": [],
    "claims": [{"statement": "Accounts are new.", "refs": ["A1", "P1", "W1"]}],
    "recommended_action": "human_review",
}  # fmt: skip
INVESTIGATION = {
    "created_at": NOW,
    "status": "done",
    "verdict": "staged",
    "report": REPORT,
    "evidence": {
        "posts": {"P1": "at://did:plc:x/app.bsky.feed.post/3kabc"},
        "pages": {"W1": "https://example.com/story"},
        "grounding": {"share": 0.9},
    },
    "model": "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "prompt_version": "v2",
    "calls": 3,
    "latency_ms": 41000,
    "error": None,
}


RECALL = {
    "suspicious": {"caught": 3, "n": 4, "share": 0.75},
    "suspicious_or_watch": {"caught": 4, "n": 4, "share": 1.0},
    "candidate": {"caught": 4, "n": 4, "share": 1.0},
}
EVAL_RESULT = {
    "run": {"campaign_set": "redteam-v1", "campaign_model": "nemotron", "hours": 3, "real_events": 1200000,
            "window_start": "2026-11-13T15:00:00+00:00", "git_commit": "abc123",
            "coverage_rule": "one cluster holds >= 50% of the campaign's accounts"},
    "campaign_recall": {d: RECALL for d in ("simple", "varied", "evasive", "all")},
    "campaigns": [{"difficulty": "evasive", "accounts": 10,
                   "narrative": "A fictional Velmora housing scandal.",
                   "best_tier": "missed", "best_score": 0, "minutes_to_candidate": None, "fired": []}],
    "real_traffic": {"clusters": 900, "by_tier": {"suspicious": 30}, "per_hour_by_tier": {"suspicious": 10.0},
                     "profiles_unavailable": 12, "accounts_checked": 5000},
    "threshold_sweep": [{"score_at_least": 50, "campaign_recall": 0.75, "campaigns_n": 4,
                         "real_clusters_flagged_per_hour": 4.2}],
}  # fmt: skip


CAMPAIGN = {
    "id": 7,
    "campaign_key": "campaign:abc",
    "created_at": NOW,
    "cluster_ids": ["text:abc", "text:def"],
    "accounts": 25,
    "title": "Personalised donation-begging network",
    "kind": "spam",
    "verdict": "staged",
    "model": "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "prompt_version": "c1",
    "calls": 1,
    "prompt_tokens": 21000,
    "latency_ms": 60000,
    "evidence": {"clusters": {"C1": "text:abc", "C2": "text:def"}, "accounts": {"A1": "h1", "A2": "h2"}},
    "report": {
        "title": "Personalised donation-begging network",
        "summary": "25 new accounts send templated donation requests to named users.",
        "relation": "one_campaign",
        "relation_reasons": ["the same accounts appear in all clusters"],
        "kind": "spam",
        "innocent_explanation": "Families genuinely asking for help.",
        "verdict": "staged",
        "confidence": "high",
        "narratives": [{"narrative": "donation requests", "refs": ["C1", "C2"]}],
        "operation": [{"statement": "One template, names swapped", "refs": ["C1", "A1"]}],
        "review_first": [
            {"ref": "A1", "reason": "in every cluster"},
            {"ref": "A2", "reason": "posted first"},
        ],
        "recommended_action": "human_review",
    },
}


STORY = {
    "id": 3,
    "created_at": NOW,
    "title": "Fighting near Tersyanka",
    "story_status": "contested",
    "cluster_ids": ["text:abc"],
    "model": "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "prompt_version": "s1",
    "calls": 1,
    "latency_ms": 40000,
    "evidence": {
        "headlines": {
            "H1": {"source": "TASS (state media)", "url": "https://pravda.me/@tass/1", "text": "Liberated"}
        },
        "news": {"N1": {"outlet": "Kyiv Independent", "language": "uk", "title": "Held", "link": "https://n/1"}},
        "clusters": {"S1": "text:abc"},
        "media": {
            "I1": {"kind": "image", "ref": "https://cdn.t.me/summit.jpg", "public": True},
            "I2": {"kind": "image", "ref": "bafimg9", "did": "did:plc:x", "public": False},
        },
    },
    "report": {
        "title": "Fighting near Tersyanka",
        "summary": "TASS says the village was taken; Ukrainian outlets say it is held.",
        "versions": [{"who": "TASS (state media)", "says": "taken", "refs": ["H1"]},
                     {"who": "Ukrainian outlets", "says": "held", "refs": ["N1"]}],
        "agreements": [],
        "media": [{"ref": "I1", "observation": "leaders at a table"}, {"ref": "I2", "observation": "kayaks"}],
        "contradictions": [{"about": "facts", "statement": "who controls it", "refs": ["H1", "N1"]}],
        "only_in_state_media": [],
        "amplification": [
            {"ref": "S1", "pushes": "state_media_version", "version": "the TASS version", "note": "new"},
            {"ref": "S2", "pushes": "unrelated", "version": "", "note": "bible verses"},
        ],
        "status": "contested", "confidence": "medium",
    },
}  # fmt: skip


class FakeRepository:
    def __init__(self, status_data: dict[str, ServiceStatus] | Exception) -> None:
        self._statuses = status_data
        self.saved_reviews: list[tuple[Any, ...]] = []
        self.saved_categories: list[str | None] = []
        self.account_reviews: list[tuple[str, str, str]] = []
        self.next_cluster: str | None = "text:next"
        self.next_asked_after: str | None = None

    async def statuses(self) -> dict[str, ServiceStatus]:
        if isinstance(self._statuses, Exception):
            raise self._statuses
        return self._statuses

    async def tier_counts(self, hours: int) -> dict[str, int]:
        return {"suspicious": 1, "likely_organic": 4}

    async def clusters(
        self, tier: str | None, hours: int, limit: int, unreviewed: bool = False
    ) -> list[dict[str, Any]]:
        self.unreviewed_asked = unreviewed
        row = dict(CLUSTER) | {"confirmed": 2, "false_alarm": 1, "unsure": 0, "reviews": 3}
        return [row] if tier in (None, "suspicious") and not unreviewed else []

    async def reviewed_count(self, hours: int) -> int:
        return 1

    async def cluster(self, cluster_id: str) -> dict[str, Any] | None:
        return dict(CLUSTER) if cluster_id == CLUSTER["id"] else None

    async def members(self, cluster_id: str) -> list[dict[str, Any]]:
        return [
            {
                "account_hash": "h1",
                "handle": "bot1.mast.example.ap.brid.gy",
                "display_name": "Bot One",
                "created_at": NOW - timedelta(days=3),
                "followers": 1,
                "follows": 1,
                "events": 2,
                "status": "active",
            },
            {
                "account_hash": "h2",
                "handle": "listedbot.mast.qixto.com.ap.brid.gy",
                "created_at": None,
                "followers": None,
                "follows": None,
                "events": 1,
                "status": "unavailable",
                "on_public_list": True,
                "did": "did:plc:listed",
            },
        ]

    async def posts(self, cluster_id: str) -> list[dict[str, Any]]:
        return [
            {
                "post_id": "at://did:plc:x/app.bsky.feed.post/3kabc",
                "posted_at": NOW,
                "text": "the exact copied text",
                "bridged": True,
                "media_refs": [{"kind": "image", "ref": "bafimg1"}, {"kind": "video", "ref": "bafvid1"}],
                "handle": "bot1.mast.example.ap.brid.gy",
                "status": "active",
                "on_public_list": False,
            },
            {
                "post_id": "at://did:plc:listed/app.bsky.feed.post/9zz",
                "posted_at": NOW,
                "text": "post by a listed account",
                "bridged": True,
                "handle": "listedbot.mast.qixto.com.ap.brid.gy",
                "status": "unavailable",
                "on_public_list": True,
            },
        ]

    async def related(self, cluster_id: str) -> list[dict[str, Any]]:
        return []

    async def investigation(self, cluster_id: str) -> dict[str, Any] | None:
        return INVESTIGATION

    async def latest_eval_run(self) -> dict[str, Any] | None:
        return {"name": "run-test", "created_at": NOW, "result": EVAL_RESULT}

    async def review_counts(self) -> list[tuple[str | None, str, str | None, int]]:
        return [("suspicious", "confirm", "staged", 3), ("suspicious", "false_alarm", "organic", 1)]

    async def nemotron_stats(self) -> dict[str, Any]:
        return {
            "versions": [
                {"prompt_version": "v2", "investigations": 5, "staged": 2, "organic": 2, "insufficient": 1,
                 "grounding": 0.93, "latency_p50_ms": 40000, "calls_per_investigation": 2.6}
            ],
            "calls_24h": {"calls": 13, "errors": 0, "retries": 1, "latency_p50_ms": 20000,
                          "prompt_tokens": 40000, "completion_tokens": 30000},
        }  # fmt: skip

    async def reviews(self, cluster_id: str) -> list[dict[str, Any]]:
        return [
            {"created_at": NOW, "decision": d, "note": "private note", "tier_at": t, "verdict_at": v,
             "category": self.saved_categories[i]}
            for i, (d, t, v) in enumerate(self.saved_reviews)
        ]  # fmt: skip

    async def add_review(
        self,
        cluster_id: str,
        decision: str,
        note: str | None,
        tier: str | None,
        verdict: str | None,
        category: str | None = None,
    ) -> None:
        self.saved_reviews.append((decision, tier, verdict))
        self.saved_categories.append(category)

    async def next_unreviewed(self, after: str | None, hours: int) -> str | None:
        self.next_asked_after = after
        return self.next_cluster

    async def review_categories(self) -> dict[str, int]:
        return {"spam": 3, "propaganda": 1}

    async def log_model_call(self, record: dict[str, Any]) -> None:
        pass

    async def live_findings(self) -> dict[str, Any]:
        return {
            "since": NOW - timedelta(hours=6),
            "flagged_clusters": 228,
            "flagged_accounts": 1216,
            "listed_accounts": 3,
            "removed_accounts": 16,
            "telegram_first_clusters": 2,
            "investigated": 260,
            "staged": 70,
            "campaigns": 5,
            "staged_campaigns": 3,
            "recycled_pictures": 4,
            "recycled_mismatch": 1,
            "unlisted_handles": [
                "newbie@truthsocial.co.in",
                "x.mast.example.ap.brid.gy",
                "nobody.bsky.social",
                "someone@mastodon.social",
            ],
            "domains": ["france.news-pravda.com", "example.org"],
        }

    async def recycled_for_cluster(self, cluster_id: str) -> list[dict[str, Any]]:
        if cluster_id != "text:abc":
            return []
        return [{
            "media_ref": "bafimg1", "hash_distance": 2, "earlier_cluster": "server:x", "earlier_at": NOW,
            "earlier_caption": "Floods in Valencia 2024", "earlier_source": "Telegram channel @news",
            "later_cluster": "text:abc", "later_at": NOW, "later_caption": "Kyiv today after the strike",
            "later_source": "Bluesky account", "supports": "earlier",
            "judgement": {"explanation": "Spanish street signs and a flooded street, no war damage."},
        }]  # fmt: skip

    async def stories(self, hours: int) -> list[dict[str, Any]]:
        return [STORY]

    async def story(self, story_id: int) -> dict[str, Any] | None:
        return STORY if story_id == 3 else None

    async def stories_for_cluster(self, cluster_id: str) -> list[dict[str, Any]]:
        report: dict[str, Any] = STORY["report"]  # type: ignore[assignment]
        evidence: dict[str, Any] = STORY["evidence"]  # type: ignore[assignment]
        row = {"id": 3, "title": STORY["title"], "story_status": "contested",
               "amplification": report["amplification"], "refs": evidence["clusters"]}  # fmt: skip
        return [row] if cluster_id == "text:abc" else []

    async def campaigns(self, hours: int) -> list[dict[str, Any]]:
        return [CAMPAIGN]

    async def campaign(self, campaign_id: int) -> dict[str, Any] | None:
        return CAMPAIGN if campaign_id == 7 else None

    async def campaign_for_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        row = {"id": 7, "title": CAMPAIGN["title"], "clusters": 2, "accounts": 25}
        return row if cluster_id in CAMPAIGN["cluster_ids"] else None  # type: ignore[operator]

    async def accounts_by_hash(self, hashes: list[str]) -> dict[str, dict[str, Any]]:
        return {
            "h1": {"handle": "beggar1.bsky.social", "status": "active", "on_public_list": False},
            "h2": {"handle": "listed.mast.example.ap.brid.gy", "status": "active", "on_public_list": True},
        }

    async def add_account_review(self, cluster_id: str, account_hash: str, decision: str) -> None:
        self.account_reviews.append((cluster_id, account_hash, decision))

    async def account_marks(self, account_hashes: list[str]) -> dict[str, dict[str, Any]]:
        latest = {h: (c, d) for c, h, d in self.account_reviews if h in account_hashes}
        return {h: {"decision": d, "cluster_id": c} for h, (c, d) in latest.items() if d != "clear"}

    async def account_review_counts(self) -> dict[str, int]:
        return {"real": 2}


def client(
    data: dict[str, ServiceStatus] | Exception | None = None,
    repo: FakeRepository | None = None,
    translator: Any = None,
) -> TestClient:
    repo = repo or FakeRepository(statuses() if data is None else data)
    return TestClient(create_app(repo, reviewer_token=TOKEN, translator=translator))


FORM = {"content-type": "application/x-www-form-urlencoded"}


def login(http: TestClient) -> None:
    ok = http.post("/review", content=f"token={TOKEN}", headers=FORM, follow_redirects=False)
    http.cookies.set("factslens_review", ok.cookies["factslens_review"])


def test_health_ok_when_all_services_report() -> None:
    with client() as http:
        response = http.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["healthy"] is True and set(body["services"]) == {
        "ingest",
        "telegram",
        "mastodon",
        "detector",
        "enrich",
        "investigator",
    }
    assert 3 <= body["last_event_seconds_ago"] <= 6


def test_health_503_when_a_service_is_stale_or_missing() -> None:
    with client(statuses(detector_age_s=600)) as http:
        stale = http.get("/health")
    with client({}) as http:
        empty = http.get("/health")
    assert stale.status_code == 503 and stale.json()["services"]["detector"]["healthy"] is False
    assert empty.status_code == 503 and empty.json()["services"]["ingest"]["reported_seconds_ago"] is None


def test_polling_sources_may_be_quiet_between_rounds() -> None:
    from factslens.api.app import build_view

    data = statuses()
    for name in ("telegram", "mastodon"):  # waiting for the next 5-minute round
        data[name] = ServiceStatus(name, NOW - timedelta(seconds=290), {})
    view = build_view(data, now=NOW)
    assert view["services"]["telegram"]["healthy"] and view["services"]["mastodon"]["healthy"]
    data["detector"] = ServiceStatus("detector", NOW - timedelta(seconds=290), {})
    assert not build_view(data, now=NOW)["services"]["detector"]["healthy"]  # the stream must not pause


def test_login_accepts_a_key_pasted_with_spaces_or_backticks() -> None:
    from urllib.parse import quote

    with client() as http:
        for pasted in (f" {TOKEN} ", f"`{TOKEN}`", f'"{TOKEN}"'):
            response = http.post(
                "/review", content=f"token={quote(pasted)}", headers=FORM, follow_redirects=False
            )
            assert response.status_code == 303, pasted
        wrong = http.post(
            "/review", content=f"token={quote(TOKEN[:-1])}", headers=FORM, follow_redirects=False
        )
        assert wrong.status_code == 200 and "not valid" in wrong.text


def test_cluster_list_shows_review_counts_and_filters_unreviewed() -> None:
    repo = FakeRepository(statuses())
    with client(repo=repo) as http:
        listing = http.get("/clusters").text
        assert "✓ 2" in listing and "✗ 1" in listing and "? 0" not in listing
        assert "1 of 5 clusters reviewed" in listing and "reviewed=no" in listing
        filtered = http.get("/clusters?tier=suspicious&reviewed=no").text
        assert repo.unreviewed_asked and "showing only clusters not reviewed yet" in filtered
        assert http.get("/clusters?reviewed=maybe").status_code == 404


def test_status_page_renders_numbers_and_tier_counts() -> None:
    with client() as http:
        page = http.get("/")
        fragment = http.get("/status/fragment")
    assert page.status_code == 200 and fragment.status_code == 200
    for text in ("FactsLens", "Live", "2,136", "Bluesky 2,100 · Telegram 6 · Mastodon 30", "40 Telegram",
                 "59 IFTAS-listed Mastodon", "example.com", "#breaking", "Likely organic"):  # fmt: skip
        assert text in page.text
    assert "<html" not in fragment.text and "example.com" in fragment.text


def test_status_page_still_renders_when_store_is_down() -> None:
    with client(RuntimeError("boom")) as http:
        page = http.get("/")
        health = http.get("/health")
    assert page.status_code == 200 and "status store unavailable" in page.text
    assert health.status_code == 503


def test_cluster_list_and_unknown_tier() -> None:
    with client() as http:
        listing = http.get("/clusters")
        bad = http.get("/clusters?tier=guilty")
    assert listing.status_code == 200 and "Same message from many new accounts" in listing.text
    assert "new_accounts" in listing.text and bad.status_code == 404


def test_cluster_page_explains_signals_and_hides_accounts_from_public() -> None:
    with client() as http:
        page = http.get("/clusters/text:abc")
        missing = http.get("/clusters/text:nope")
    assert page.status_code == 200 and missing.status_code == 404
    for text in (
        "Suspicious",
        "90%",
        "≥ 50%",
        "created in the last 30 days",
        "not an accusation",
        "removed from Bluesky",
    ):
        assert text in page.text
    assert "bot1.mast.example.ap.brid.gy" not in page.text  # no account names publicly
    assert "the exact copied text" not in page.text  # no individual posts publicly
    assert "bridged from Mastodon" in page.text  # aggregate context is fine


def test_reviewer_token_unlocks_names_and_posts() -> None:
    with client() as http:
        wrong = http.post(
            "/review", content="token=nope", headers={"content-type": "application/x-www-form-urlencoded"}
        )
        assert "not valid" in wrong.text
        ok = http.post(
            "/review",
            content=f"token={TOKEN}",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert ok.status_code == 303
        http.cookies.set("factslens_review", ok.cookies["factslens_review"])
        page = http.get("/clusters/text:abc")
    assert "bot1.mast.example.ap.brid.gy" in page.text and "the exact copied text" in page.text
    assert "https://bsky.app/profile/bot1.mast.example.ap.brid.gy/post/3kabc" in page.text


def test_helpers() -> None:
    assert (
        post_url("at://did:plc:x/app.bsky.feed.post/3k", None) == "https://bsky.app/profile/did:plc:x/post/3k"
    )
    assert post_url("garbage", "h") is None
    assert sparkline([]) == "" and len(sparkline([0, 5, 2]).split()) == 3


def test_cluster_page_shows_the_nemotron_report_with_linked_evidence() -> None:
    with client() as http:
        page = http.get("/clusters/text:abc")
    for text in (
        "Nemotron investigation",
        "Staged",
        "Friends sharing the same news story",
        "Fear appeal",
        "they are coming",
        "registered 12 days ago",
        "90% of its references checked",
        'href="https://example.com/story"',  # web pages are public evidence
    ):
        assert text in page.text, text
    assert "bsky.app/profile/did:plc:x/post/3kabc" not in page.text  # posts only for reviewers


def test_cluster_page_shows_what_nemotron_searched_in_the_news() -> None:
    news_checks = [
        {"claim": "A port strike", "explanation": "Only listed sites call it a collapse.",
         "finding": "real_event_distorted", "refs": ["N2"]},
        {"claim": "A second claim", "explanation": "Nothing found.", "finding": "no_coverage_found",
         "refs": ["Q2"]},
    ]  # fmt: skip
    evidence = cast(dict[str, Any], INVESTIGATION["evidence"]) | {
        "searches": {
            "Q1": {"query": "port strike", "language": "fr", "results": 2},
            "Q2": {"query": "strike debunk", "language": "en", "results": None},
        },
        "news": {
            "N1": {"search": "Q1", "outlet": "Example Times", "site": "times.example", "title": "Strike ends",
                   "published": "2026-11-14T08:30:00+00:00", "link": "https://news.example/1",
                   "on_propaganda_lists": False},
            "N2": {"search": "Q1", "outlet": "Pravda FR", "site": "fr.news-pravda.com", "title": "Chaos",
                   "published": None, "link": "https://news.example/2", "on_propaganda_lists": True},
        },
    }  # fmt: skip
    investigation = INVESTIGATION | {"report": REPORT | {"news_checks": news_checks}, "evidence": evidence}

    class Repo(FakeRepository):
        async def investigation(self, cluster_id: str) -> dict[str, Any] | None:
            return investigation

    with client(repo=Repo(statuses())) as http:
        page = http.get("/clusters/text:abc").text
    for text in (
        "Independent news check",
        "real event distorted",
        "Only listed sites call it a collapse.",
        "A port strike",
        "no coverage found",
        "searched “port strike” (fr)",
        "Example Times: Strike ends",
        "(2026-11-14)",
        "on propaganda lists",
        "search failed",
        'href="https://news.example/2"',
    ):
        assert text in page, text


def test_media_is_shown_blurred_to_reviewers_only() -> None:
    report = REPORT | {"media": [{"ref": "I1", "observation": "AI-generated portrait with a watermark"}]}
    evidence = cast(dict[str, Any], INVESTIGATION["evidence"]) | {"images": {"I1": "bafimg1"}}
    investigation = INVESTIGATION | {"report": report, "evidence": evidence}

    class Repo(FakeRepository):
        async def investigation(self, cluster_id: str) -> dict[str, Any] | None:
            return investigation

    thumb = "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:x/bafimg1@jpeg"
    with client(repo=Repo(statuses())) as http:
        public = http.get("/clusters/text:abc").text
        login(http)
        reviewer = http.get("/clusters/text:abc").text
    assert thumb not in public  # the post is not a public case
    assert thumb in reviewer and 'class="media"' in reviewer and 'referrerpolicy="no-referrer"' in reviewer
    assert (
        'href="https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:x/bafimg1@jpeg"' in reviewer
    )  # I1 links
    assert "https://video.bsky.app/watch/did:plc:x/bafvid1/thumbnail.jpg" in reviewer


def test_review_bar_types_skip_and_moves_on() -> None:
    repo = FakeRepository(statuses())
    with client(repo=repo) as http:
        login(http)
        page = http.get("/clusters/text:abc?saved=text:prev").text
        assert 'class="reviewbar"' in page and 'value="confirm:propaganda"' in page and 'value="skip"' in page
        assert "Decision saved" in page and 'href="/clusters/text:prev#review"' in page
        spam = http.post(
            "/clusters/text:abc/review", content="choice=confirm%3Aspam", headers=FORM, follow_redirects=False
        )
        assert spam.status_code == 303 and repo.saved_reviews[-1][0] == "confirm"
        assert repo.saved_categories == ["spam"] and repo.next_asked_after == "text:abc"
        skip = http.post(
            "/clusters/text:abc/review", content="choice=skip", headers=FORM, follow_redirects=False
        )
        assert (
            skip.headers["location"] == "/clusters/text:next" and len(repo.saved_reviews) == 1
        )  # nothing stored
        unsure = http.post(
            "/clusters/text:abc/review", content="choice=unsure%3Aspam", headers=FORM, follow_redirects=False
        )
        assert (
            unsure.status_code == 303 and repo.saved_categories[-1] is None
        )  # a type only goes with confirm
        bad = http.post("/clusters/text:abc/review", content="choice=confirm%3Aguilty", headers=FORM)
        assert bad.status_code == 400
        repo.next_cluster = None
        done = http.post(
            "/clusters/text:abc/review", content="choice=skip", headers=FORM, follow_redirects=False
        )
        assert done.headers["location"] == "/clusters?reviewed=no"  # everything reviewed
        assert "Spam / scam" in http.get("/clusters/text:abc").text
        metrics = http.get("/metrics").text
    assert "Spam / scam" in metrics and "Propaganda / influence operation" in metrics


class FakeTranslator:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        from factslens.investigator.llm import BudgetExhausted, ChatResult

        self.calls += 1
        if self.fail:
            raise BudgetExhausted("300 model calls in the last hour")
        ids = [json.loads(line)["id"] for line in messages[1]["content"].splitlines()[1:]]
        items = [{"id": i, "language": "es", "english": f"english {i}"} for i in ids]
        return ChatResult({"role": "assistant", "content": json.dumps({"items": items})}, "stop", 10, 10, 0)


def test_reviewers_can_translate_posts_with_nemotron() -> None:
    translator = FakeTranslator()
    with client(translator=translator) as http:
        assert http.post("/clusters/text:abc/translate").status_code == 403
        assert "Translate to English" not in http.get("/clusters/text:abc").text
        login(http)
        assert "Translate to English (Nemotron)" in http.get("/clusters/text:abc").text
        done = http.post("/clusters/text:abc/translate", follow_redirects=False)
        assert done.status_code == 303 and done.headers["location"] == "/clusters/text:abc#posts"
        page = http.get("/clusters/text:abc").text
        assert "es → English, by Nemotron" in page and "english T1" in page  # the shared message
        assert "es → English</span> english T2" in page  # the first post
        http.post("/clusters/text:abc/translate", follow_redirects=False)
        assert translator.calls == 1  # already translated: no second call


def test_translation_failure_is_shown_not_raised() -> None:
    with client(translator=FakeTranslator(fail=True)) as http:
        login(http)
        done = http.post("/clusters/text:abc/translate", follow_redirects=False)
        assert done.headers["location"].endswith("?translate=failed#posts")
        assert "Translation is not available right now" in http.get(done.headers["location"]).text


def test_reviewers_can_mark_single_accounts_as_exceptions() -> None:
    repo = FakeRepository(statuses())
    with client(repo=repo) as http:
        assert (
            http.post(
                "/clusters/text:abc/accounts/h1/review", content="decision=real", headers=FORM
            ).status_code
            == 403
        )
        login(http)
        page = http.get("/clusters/text:abc").text
        assert 'action="/clusters/text:abc/accounts/h1/review"' in page and 'value="real"' in page
        done = http.post(
            "/clusters/text:abc/accounts/h1/review",
            content="decision=real",
            headers=FORM,
            follow_redirects=False,
        )
        assert done.status_code == 303 and done.headers["location"] == "/clusters/text:abc#accounts"
        assert repo.account_reviews == [("text:abc", "h1", "real")]
        page = http.get("/clusters/text:abc").text
        assert "reviewer: real person" in page and 'value="clear"' in page
        unknown = http.post(
            "/clusters/text:abc/accounts/nobody/review", content="decision=real", headers=FORM
        )
        assert unknown.status_code == 404  # only accounts of this cluster
        bad = http.post("/clusters/text:abc/accounts/h1/review", content="decision=guilty", headers=FORM)
        assert bad.status_code == 400
        assert "2 accounts marked as real people" in http.get("/metrics").text


def test_campaign_pages_link_clusters_and_hide_names_from_the_public() -> None:
    with client() as http:
        listing = http.get("/campaigns").text
        assert "Personalised donation-begging network" in listing and 'href="/campaigns/7"' in listing
        public = http.get("/campaigns/7").text
        assert "Accounts to review first" in public and 'href="/clusters/text:abc"' in public
        assert "beggar1.bsky.social" not in public  # not a public case
        assert "listed.mast.example.ap.brid.gy" in public  # on IFTAS's public list
        assert http.get("/campaigns/8").status_code == 404
        assert 'Part of a campaign: <a href="/campaigns/7">' in http.get("/clusters/text:abc").text
        login(http)
        assert "beggar1.bsky.social" in http.get("/campaigns/7").text


def test_metrics_page_shows_live_findings_with_units() -> None:
    from factslens.enrich.lists import KnownLists

    lists = KnownLists(
        domains={"news-pravda.com": "checkfirst"},
        servers={"truthsocial.co.in", "mast.example", "mastodon.social"},
        server_accounts={"truthsocial.co.in": 392, "mast.example": 12, "mastodon.social": 1},
        loaded=True,
    )
    with TestClient(create_app(FakeRepository(statuses()), reviewer_token=TOKEN, lists=lists)) as http:
        page = http.get("/metrics").text
    assert "Live findings since" in page and ">228<" in page and ">1216<" in page
    # mastodon.social has one listed account: a big general server, so its users are not counted
    assert '(truthsocial.co.in 1, mast.example 1)</span></td><td class="num">2<' in page
    assert (
        "france.news-pravda.com" in page
        and "example.org" not in page.split("Live findings")[1].split("</table>")[0]
    )
    assert "since removed by the platform" in page


def test_story_pages_show_versions_and_link_the_cluster_both_ways() -> None:
    with client() as http:
        assert "Fighting near Tersyanka" in http.get("/stories").text
        page = http.get("/stories/3").text
        assert (
            "Who says what" in page
            and "TASS (state media)" in page
            and "Contested: versions contradict" in page
        )
        assert 'href="/clusters/text:abc"' in page and 'href="https://n/1"' in page
        assert http.get("/stories/4").status_code == 404
        cluster = http.get("/clusters/text:abc").text
        assert (
            'In a contested story: <a href="/stories/3">' in cluster and "<b>the TASS version</b>" in cluster
        )
        assert "Also matched by keyword, but not about this story" in page
        assert 'src="https://cdn.t.me/summit.jpg"' in page  # public Telegram photo
        assert "bafimg9" not in page  # a photo from an account's post: reviewers only
        login(http)
        assert (
            "https://cdn.bsky.app/img/feed_thumbnail/plain/did:plc:x/bafimg9@jpeg"
            in http.get("/stories/3").text
        )


def test_cluster_page_shows_recycled_pictures_with_captions_for_reviewers_only() -> None:
    with client() as http:
        public = http.get("/clusters/text:abc").text
        assert "Recycled pictures" in public and "the earlier caption, not the later one" in public
        assert "Spanish street signs" in public and "Floods in Valencia" not in public  # captions: reviewers
        login(http)
        assert "Floods in Valencia 2024" in http.get("/clusters/text:abc").text


def test_review_needs_reviewer_mode_and_is_stored_with_context() -> None:
    repo = FakeRepository(statuses())
    with client(repo=repo) as http:
        assert (
            http.post("/clusters/text:abc/review", content="decision=confirm", headers=FORM).status_code
            == 403
        )
        login(http)
        assert (
            http.post("/clusters/text:abc/review", content="decision=guilty", headers=FORM).status_code == 400
        )
        done = http.post(
            "/clusters/text:abc/review",
            content="decision=false_alarm&note=fans",
            headers=FORM,
            follow_redirects=False,
        )
        assert done.status_code == 303 and done.headers["location"] == "/clusters/text:next?saved=text%3Aabc"
        reviewer_page = http.get("/clusters/text:abc")
    assert repo.saved_reviews == [("false_alarm", "suspicious", "staged")]
    assert "False alarm" in reviewer_page.text and "private note" in reviewer_page.text
    assert "bsky.app/profile/did:plc:x/post/3kabc" in reviewer_page.text
    with client(repo=repo) as http:
        public = http.get("/clusters/text:abc")
    assert "False alarm" in public.text and "private note" not in public.text


def test_metrics_page_shows_units_and_sample_sizes() -> None:
    with client() as http:
        page = http.get("/metrics")
    assert page.status_code == 200
    for text in (
        '75% <span class="muted">(3/4)</span>',  # campaign recall with n
        "A fictional Velmora housing scandal.",
        "missed",
        "1,200,000 real events",
        "10.0/h",
        "4.2",
        '75% <span class="muted">(3/4)</span>',  # human precision with n
        "v2",
        "93%",
    ):
        assert text in page.text, text


def test_already_public_cases_are_named_publicly_others_stay_hidden() -> None:
    with client() as http:
        page = http.get("/clusters/text:abc")
    assert "listedbot.mast.qixto.com.ap.brid.gy" in page.text and "on IFTAS public list" in page.text
    assert "post by a listed account" in page.text
    assert "bot1.mast.example.ap.brid.gy" not in page.text and "the exact copied text" not in page.text
