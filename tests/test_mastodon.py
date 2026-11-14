from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from factslens.common.models import Event, EventKind, Platform
from factslens.detector.groups import SwarmDetector, origin_server, post_key
from factslens.detector.scoring import Profile, declares_automation
from factslens.enrich.lists import KnownLists, account_server, bridgy_handle
from factslens.enrich.profiles import (
    MastodonAccountUnavailable,
    MastodonProfileClient,
    parse_mastodon_account,
)
from factslens.ingest.mastodon import MastodonSource, listed_servers, parse_content, status_event

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def status(n: int, username: str = "cugicpewux", minutes_ago: float = 5, **extra: Any) -> dict[str, Any]:
    return {
        "id": str(117614000000000000 + n),
        "url": f"https://social.example/@{username}/{117614000000000000 + n}",
        "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z"),
        "language": "es",
        "content": (
            '<p>VOCES OCCIDENTALES: Europa entre sanciones <a href="https://social.example/tags/europa" '
            'class="mention hashtag" rel="tag">#<span>europa</span></a></p><p>Fuente: '
            '<a href="https://www.lesoir.be/782489/article" rel="nofollow noopener" target="_blank">'
            '<span class="invisible">https://www.</span><span class="ellipsis">lesoir.be/782489</span>'
            '<span class="invisible">/article</span></a><br>cc <span class="h-card"><a '
            'href="https://other.example/@friend" class="u-url mention">@<span>friend</span></a></span></p>'
        ),
        "spoiler_text": "",
        "tags": [{"name": "Europa"}],
        "mentions": [{"acct": "friend@other.example"}],
        "media_attachments": [
            {
                "type": "image",
                "url": "https://cdn.example/full.jpg",
                "preview_url": "https://cdn.example/small.jpg",
            }
        ],
        "card": None,
        "in_reply_to_id": None,
        "reblog": None,
        "account": {"username": username, "acct": username},
    } | extra


def test_parse_content_keeps_text_and_real_links_only() -> None:
    text, links = parse_content(status(1)["content"])
    assert text.startswith(
        "VOCES OCCIDENTALES: Europa entre sanciones #europa\n\nFuente: https://www.lesoir.be"
    )
    assert "\ncc @friend" in text
    assert links == ["https://www.lesoir.be/782489/article"]  # hashtag and mention links skipped


def test_status_event_fields() -> None:
    event = status_event(status(1, username="CugicPewux"), "social.example")
    assert event is not None and event.platform is Platform.MASTODON and event.kind is EventKind.POST
    assert event.author == "mastodon:cugicpewux@social.example"
    assert event.id == "https://social.example/@CugicPewux/117614000000000001"
    assert event.hashtags == ("europa",) and event.langs == ("es",)
    assert event.links == ("https://www.lesoir.be/782489/article",)
    assert event.media[0].ref == "https://cdn.example/small.jpg"  # the preview is enough for the model
    assert status_event(status(2, reblog={"id": "1"}), "social.example") is None  # boosts are not posts


def test_listed_servers_most_observed_first() -> None:
    rows = [
        {"instance": "b.example", "iftas_observations": "3"},
        {"instance": "A.example", "iftas_observations": "9"},
    ]
    assert listed_servers(rows) == ["a.example", "b.example"]


class FakeFediverse:
    """Two Mastodon servers (one down) and the Bluesky AppView."""

    def __init__(self) -> None:
        self.requests: list[httpx.URL] = []
        self.timeline = [
            status(3, "tubuqe"),
            status(2, "bridged_user"),
            status(1, "tubuqe", minutes_ago=300),  # older than 90 minutes: skipped
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url)
        if request.url.host == "down.example":
            return httpx.Response(503)
        if request.url.host == "public.api.bsky.app":
            actors = request.url.params.get_list("actors")
            assert "bridged-user.social.example.ap.brid.gy" in actors  # underscore written as hyphen
            return httpx.Response(
                200, json={"profiles": [{"handle": "bridged-user.social.example.ap.brid.gy"}]}
            )
        if request.url.params.get("min_id"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=self.timeline)


async def test_source_skips_old_posts_and_accounts_active_on_bluesky() -> None:
    fediverse = FakeFediverse()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fediverse))
    source = MastodonSource(
        ["down.example", "social.example"], poll_seconds=0, client=client, clock=lambda: NOW.timestamp()
    )
    seen: list[str] = []
    rounds = 0
    async for item in source.items(None):
        if item.event is None:
            rounds += 1
            if rounds == 2:
                break
            continue
        seen.append(item.event.author)
    assert seen == ["mastodon:tubuqe@social.example"]
    assert source.skipped_bridged == 1 and source.errors == 1  # the down server failed once...
    assert sum(u.host == "down.example" for u in fediverse.requests) == 1  # ...and waits before retrying
    timeline_calls = [u for u in fediverse.requests if u.host == "social.example"]
    assert timeline_calls[1].params["min_id"] == "117614000000000003"  # second round continues
    appview_calls = [u for u in fediverse.requests if u.host == "public.api.bsky.app"]
    assert len(appview_calls) == 1  # bridge status is cached


def mastodon_event(n: int, user: str = "u") -> Event:
    return Event(
        id=f"https://Social.example/@{user}/{n}",
        platform=Platform.MASTODON,
        kind=EventKind.POST,
        author=f"mastodon:{user}@social.example",
        time_us=1_000_000 * n,
        text="same words from the network " * 3,
    )


def test_bluesky_copy_of_a_mastodon_post_is_dropped() -> None:
    copy = Event(
        id="at://did:plc:x/app.bsky.feed.post/1",
        platform=Platform.BLUESKY,
        kind=EventKind.POST,
        author="did:plc:x",
        time_us=2_000_000,
        text="same words from the network " * 3,
        origin_url="https://social.example/@u/1",
    )
    assert post_key(copy) == post_key(mastodon_event(1)) == "social.example/@u/1"
    assert origin_server(mastodon_event(1)) == origin_server(copy) == "social.example"
    detector = SwarmDetector()
    detector.add(mastodon_event(1))
    detector.add(copy)
    detector.add(mastodon_event(2))  # a different post from the same account still counts
    assert detector.duplicates == 1 and detector.events == 2
    thread = [
        copy.model_copy(update={"origin_url": f"https://forum.example/t/topic/45#post_{n}"}) for n in (5, 6)
    ]
    assert post_key(thread[0]) != post_key(thread[1])  # replies in one thread stay apart


def test_lists_know_mastodon_accounts_and_servers() -> None:
    assert bridgy_handle("alive.bar", "rus_vesna") == "rus-vesna.alive.bar.ap.brid.gy"
    assert account_server("hahiraz@truthsocial.co.in") == "truthsocial.co.in"
    assert account_server("u.mast.example.ap.brid.gy") == "mast.example"
    lists = KnownLists(mastodon_accounts={"hahiraz@truthsocial.co.in"}, servers={"truthsocial.co.in"})
    assert lists.is_known_account(None, "Hahiraz@truthsocial.co.in")
    assert lists.on_listed_server("newcomer@truthsocial.co.in")


ACCOUNT = {
    "username": "sophiaendo_mlp",
    "display_name": "Sophia",
    "note": "<p>Love &amp; peace</p>",
    "created_at": "2026-10-09T00:00:00.000Z",
    "followers_count": 2,
    "following_count": 0,
    "statuses_count": 279,
    "avatar": "https://mstdn.example/avatars/original/missing.png",
    "header": "https://cdn.example/header.jpg",
    "bot": True,
}


def test_mastodon_account_becomes_a_profile_with_its_bot_flag() -> None:
    profile = parse_mastodon_account(ACCOUNT, "mastodon:sophiaendo_mlp@mstdn.example")
    assert profile is not None and profile.handle == "sophiaendo_mlp@mstdn.example"
    assert profile.description == "Love & peace" and profile.posts == 279
    assert profile.has_avatar is False and profile.has_banner is True and profile.labels == ("bot",)
    assert declares_automation(Profile(available=True, handle=profile.handle, labels=profile.labels))
    assert not declares_automation(Profile(available=True, handle="sophia@mstdn.example"))


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        (httpx.Response(200, json=ACCOUNT), "profile"),
        (httpx.Response(404), "unavailable"),
        (httpx.Response(200, json=ACCOUNT | {"suspended": True}), "unavailable"),
        (httpx.Response(502), "error"),
    ],
)
def test_mastodon_profile_client(response: httpx.Response, outcome: str) -> None:
    asked: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url)
        return response

    client = MastodonProfileClient(httpx.Client(transport=httpx.MockTransport(handler)))
    author = "mastodon:sophiaendo_mlp@mstdn.example"
    if outcome == "profile":
        assert client.fetch(author).followers == 2
    elif outcome == "unavailable":
        with pytest.raises(MastodonAccountUnavailable):
            client.fetch(author)
    else:
        with pytest.raises(httpx.HTTPError):
            client.fetch(author)
    assert str(asked[0]) == "https://mstdn.example/api/v1/accounts/lookup?acct=sophiaendo_mlp"
