from __future__ import annotations

from pathlib import Path

import httpx

from factslens.enrich.lists import KnownLists, bridged_server
from factslens.enrich.profiles import ProfileClient, parse_profile


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def lists(tmp_path: Path) -> KnownLists:
    write(
        tmp_path / "domains.csv",
        "domain,list,created,source\nnews-pravda.com,checkfirst_pravda,,x\npiter-news.net,viginum_portal_kombat,2013,x\n",
    )
    write(tmp_path / "telegram_channels.csv", "channel,pravda_articles,pravda_domains\nrtenfrancais,10,2\n")
    write(
        tmp_path / "accounts.csv",
        "mastodon_url,instance,username,bluesky_handle,iftas_status,did,bluesky_status\n"
        "https://mast.qixto.com/@epowo-uzata,mast.qixto.com,epowo-uzata,epowo-uzata.mast.qixto.com.ap.brid.gy,Limited,did:plc:known,suspended\n",
    )
    write(tmp_path / "instances.csv", "instance,iftas_observations\nmast.qixto.com,10\n")
    return KnownLists.load(tmp_path)


def test_bridged_server_from_handle() -> None:
    assert bridged_server("epowo-uzata.mast.qixto.com.ap.brid.gy") == "mast.qixto.com"
    assert bridged_server("alice.bsky.social") is None
    assert bridged_server(None) is None


def test_lists_match_domains_channels_accounts_and_servers(tmp_path: Path) -> None:
    known = lists(tmp_path)
    assert known.loaded
    assert known.domain_hits(["france.news-pravda.com", "www.piter-news.net", "example.com"]) == [
        "france.news-pravda.com",
        "www.piter-news.net",
    ]
    assert known.channel_hits(
        ["https://t.me/RTenfrancais/123", "https://t.me/s/rtenfrancais", "https://t.me/other"]
    ) == [
        "RTenfrancais",
        "rtenfrancais",
    ]
    assert known.is_known_account("did:plc:known", None)
    assert known.is_known_account(None, "EPOWO-UZATA.mast.qixto.com.ap.brid.gy")
    assert not known.is_known_account("did:plc:other", "someone.bsky.social")
    assert known.on_listed_server("newbot.mast.qixto.com.ap.brid.gy")
    assert not known.on_listed_server("newbot.mastodon.social.ap.brid.gy")


def test_missing_lists_disable_provenance(tmp_path: Path) -> None:
    assert not KnownLists.load(tmp_path / "nothing-here").loaded


def test_parse_profile_tolerates_odd_fields() -> None:
    profile = parse_profile(
        {
            "did": "did:plc:a",
            "handle": "a.bsky.social",
            "createdAt": "2026-11-01T10:00:00.000Z",
            "followersCount": 3,
            "followsCount": "many",
            "avatar": "https://cdn/x",
            "labels": [{"val": "spam"}, "junk"],
        }
    )
    assert profile is not None
    assert profile.created_at is not None and profile.created_at.year == 2026
    assert (profile.followers, profile.follows, profile.has_avatar, profile.has_banner) == (
        3,
        None,
        True,
        False,
    )
    assert profile.labels == ("spam",)
    assert parse_profile({"handle": "no-did"}) is None


def test_client_batches_by_25_and_omits_removed_accounts() -> None:
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        actors = request.url.params.get_list("actors")
        requested.append(len(actors))
        return httpx.Response(200, json={"profiles": [{"did": a} for a in actors if not a.endswith("gone")]})

    client = ProfileClient(
        httpx.Client(base_url="https://appview.test", transport=httpx.MockTransport(handler))
    )
    dids = [f"did:plc:{n}" for n in range(30)] + ["did:plc:gone"]
    profiles = client.fetch(dids)
    assert requested == [25, 6]
    assert len(profiles) == 30 and "did:plc:gone" not in profiles


def test_client_retries_rate_limits() -> None:
    responses = iter(
        [httpx.Response(429, headers={"ratelimit-reset": "0"}), httpx.Response(200, json={"profiles": []})]
    )
    client = ProfileClient(
        httpx.Client(
            base_url="https://appview.test", transport=httpx.MockTransport(lambda _: next(responses))
        )
    )
    assert client.fetch(["did:plc:a"]) == {}
    assert client.calls == 2 and client.rate_limited == 1
