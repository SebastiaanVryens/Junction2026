from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

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


class FakeRepository:
    def __init__(self, status_data: dict[str, ServiceStatus] | Exception) -> None:
        self._statuses = status_data

    async def statuses(self) -> dict[str, ServiceStatus]:
        if isinstance(self._statuses, Exception):
            raise self._statuses
        return self._statuses

    async def tier_counts(self, hours: int) -> dict[str, int]:
        return {"suspicious": 1, "likely_organic": 4}

    async def clusters(self, tier: str | None, hours: int, limit: int) -> list[dict[str, Any]]:
        return [dict(CLUSTER)] if tier in (None, "suspicious") else []

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
                "handle": None,
                "created_at": None,
                "followers": None,
                "follows": None,
                "events": 1,
                "status": "unavailable",
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
            }
        ]

    async def related(self, cluster_id: str) -> list[dict[str, Any]]:
        return []


def client(data: dict[str, ServiceStatus] | Exception | None = None) -> TestClient:
    return TestClient(create_app(FakeRepository(statuses() if data is None else data), reviewer_token=TOKEN))


def test_health_ok_when_all_services_report() -> None:
    with client() as http:
        response = http.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["healthy"] is True and set(body["services"]) == {"ingest", "detector", "enrich"}
    assert 3 <= body["last_event_seconds_ago"] <= 6


def test_health_503_when_a_service_is_stale_or_missing() -> None:
    with client(statuses(detector_age_s=600)) as http:
        stale = http.get("/health")
    with client({}) as http:
        empty = http.get("/health")
    assert stale.status_code == 503 and stale.json()["services"]["detector"]["healthy"] is False
    assert empty.status_code == 503 and empty.json()["services"]["ingest"]["reported_seconds_ago"] is None


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
