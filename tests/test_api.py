from __future__ import annotations

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
            {"total": 2100, "last_time_us": int((time.time() - 4) * 1e6), "reconnects": 1},
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
        "telegram": ServiceStatus("telegram", NOW - timedelta(seconds=3), {"total": 12, "polls": 40}),
        "mastodon": ServiceStatus("mastodon", NOW - timedelta(seconds=4), {"total": 30, "polls": 59}),
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


class FakeRepository:
    def __init__(self, status_data: dict[str, ServiceStatus] | Exception) -> None:
        self._statuses = status_data
        self.saved_reviews: list[tuple[Any, ...]] = []

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
                "handle": "bot1.mast.example.ap.brid.gy",
                "display_name": "Bot One",
                "created_at": NOW - timedelta(days=3),
                "followers": 1,
                "follows": 1,
                "events": 2,
                "status": "active",
            },
            {
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
            {"created_at": NOW, "decision": d, "note": "private note", "tier_at": t, "verdict_at": v}
            for d, t, v in self.saved_reviews
        ]

    async def add_review(
        self, cluster_id: str, decision: str, note: str | None, tier: str | None, verdict: str | None
    ) -> None:
        self.saved_reviews.append((decision, tier, verdict))


def client(
    data: dict[str, ServiceStatus] | Exception | None = None, repo: FakeRepository | None = None
) -> TestClient:
    repo = repo or FakeRepository(statuses() if data is None else data)
    return TestClient(create_app(repo, reviewer_token=TOKEN))


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
    for text in ("FactsLens", "Live", "110.0", "example.com", "#breaking", "2,100", "Likely organic"):
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
        assert done.status_code == 303 and done.headers["location"].endswith("#review")
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
