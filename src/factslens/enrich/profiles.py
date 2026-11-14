"""Bluesky account profiles from the public AppView (no login), 25 accounts per request."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import httpx

log = logging.getLogger(__name__)

APPVIEW = "https://public.api.bsky.app/xrpc"
BATCH = 25  # the endpoint's maximum (verified 14 Nov 2026)


@dataclass(frozen=True)
class FetchedProfile:
    did: str
    handle: str | None
    display_name: str | None
    description: str | None
    created_at: datetime | None
    followers: int | None
    follows: int | None
    posts: int | None
    has_avatar: bool
    has_banner: bool
    labels: tuple[str, ...]


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _str(value: object, limit: int = 1000) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def parse_profile(raw: dict[str, object]) -> FetchedProfile | None:
    did = raw.get("did")
    if not isinstance(did, str):
        return None
    labels = raw.get("labels")
    return FetchedProfile(
        did=did,
        handle=_str(raw.get("handle"), 253),
        display_name=_str(raw.get("displayName"), 200),
        description=_str(raw.get("description")),
        created_at=_datetime(raw.get("createdAt")),
        followers=_int(raw.get("followersCount")),
        follows=_int(raw.get("followsCount")),
        posts=_int(raw.get("postsCount")),
        has_avatar=bool(raw.get("avatar")),
        has_banner=bool(raw.get("banner")),
        labels=tuple(
            str(label["val"])
            for label in (labels if isinstance(labels, list) else [])
            if isinstance(label, dict) and "val" in label
        )[:20],
    )


class ProfileClient:
    def __init__(self, client: httpx.Client | None = None, max_attempts: int = 5) -> None:
        self._client = client or httpx.Client(
            base_url=APPVIEW, timeout=20, headers={"User-Agent": "FactsLens/0.1"}
        )
        self._max_attempts = max_attempts
        self.calls = 0
        self.rate_limited = 0

    def fetch(self, dids: Sequence[str]) -> dict[str, FetchedProfile]:
        """Profiles by DID. Accounts Bluesky no longer serves (suspended, deleted) are absent."""
        result: dict[str, FetchedProfile] = {}
        for start in range(0, len(dids), BATCH):
            batch = list(dids[start : start + BATCH])
            for profile in self._fetch_batch(batch):
                result[profile.did] = profile
        return result

    def _fetch_batch(self, dids: list[str]) -> list[FetchedProfile]:
        for attempt in range(self._max_attempts):
            self.calls += 1
            response = self._client.get("/app.bsky.actor.getProfiles", params=[("actors", d) for d in dids])
            if response.status_code == 200:
                profiles = response.json().get("profiles", [])
                return [p for raw in profiles if isinstance(raw, dict) and (p := parse_profile(raw))]
            if response.status_code == 429 or response.status_code >= 500:
                self.rate_limited += response.status_code == 429
                reset = response.headers.get("ratelimit-reset")
                wait = max(1.0, float(reset) - time.time()) if reset and reset.isdigit() else 2.0**attempt
                log.warning(
                    "appview busy; waiting", extra={"status": response.status_code, "wait_s": min(wait, 60)}
                )
                time.sleep(min(wait, 60))
                continue
            response.raise_for_status()
        raise RuntimeError(f"AppView kept failing after {self._max_attempts} attempts")
