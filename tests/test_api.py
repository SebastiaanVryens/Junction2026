from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from factslens.api.app import create_app
from factslens.common.db import ServiceStatus


def statuses(ingest_age_s: float = 2, detector_age_s: float = 3) -> dict[str, ServiceStatus]:
    now = datetime.now(UTC)
    return {
        "ingest": ServiceStatus(
            "ingest",
            now - timedelta(seconds=ingest_age_s),
            {
                "events": {"post": 1200, "repost": 900},
                "last_time_us": int((time.time() - 4) * 1e6),
                "reconnects": 1,
            },
        ),
        "detector": ServiceStatus(
            "detector",
            now - timedelta(seconds=detector_age_s),
            {
                "consumer_lag": 12,
                "events_last_minute": {"post": 3000, "repost": 3600},
                "top_domains_10m": [["example.com", 42]],
                "top_hashtags_10m": [["breaking", 7]],
            },
        ),
    }


def client(data: dict[str, ServiceStatus] | Exception) -> TestClient:
    async def provider() -> dict[str, ServiceStatus]:
        if isinstance(data, Exception):
            raise data
        return data

    return TestClient(create_app(provider))


def test_health_ok_when_all_services_report() -> None:
    with client(statuses()) as http:
        response = http.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["healthy"] is True
    assert body["services"]["ingest"]["healthy"] and body["services"]["detector"]["healthy"]
    assert 3 <= body["last_event_seconds_ago"] <= 6


def test_health_503_when_a_service_is_stale_or_missing() -> None:
    with client(statuses(detector_age_s=600)) as http:
        stale = http.get("/health")
    with client({}) as http:
        empty = http.get("/health")
    assert stale.status_code == 503 and stale.json()["services"]["detector"]["healthy"] is False
    assert empty.status_code == 503 and empty.json()["services"]["ingest"]["reported_seconds_ago"] is None


def test_status_page_renders_numbers() -> None:
    with client(statuses()) as http:
        page = http.get("/")
        fragment = http.get("/status/fragment")
    assert page.status_code == 200 and fragment.status_code == 200
    for text in ("FactsLens", "Live", "110.0", "example.com", "#breaking", "1,200"):
        assert text in page.text
    assert "<html" not in fragment.text and "example.com" in fragment.text


def test_status_page_still_renders_when_store_is_down() -> None:
    with client(RuntimeError("boom")) as http:
        page = http.get("/")
        health = http.get("/health")
    assert page.status_code == 200 and "status store unavailable" in page.text
    assert health.status_code == 503
