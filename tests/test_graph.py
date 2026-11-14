from __future__ import annotations

from typing import Any

from factslens.api.graph import Graph, add_campaign, add_cluster_links, add_clusters, add_story
from factslens.enrich.lists import KnownLists

LISTS = KnownLists(domains={"news-pravda.com": "checkfirst"}, loaded=True)
CLUSTERS: list[dict[str, Any]] = [
    {"id": "text:a", "kind": "text", "label": "Same claim", "tier": "suspicious", "accounts": 3,
     "domains": [["fr.news-pravda.com", 3], ["bsky.app", 2], ["example.org", 1]]},
    {"id": "server:x.example", "kind": "server", "label": "x.example", "tier": "watch", "accounts": 2,
     "domains": []},
]  # fmt: skip
MEMBERS: list[dict[str, Any]] = [
    {"cluster_id": "text:a", "account_hash": "h1aaaaaa", "handle": "bot1.bsky.social", "status": "active"},
    {"cluster_id": "text:a", "account_hash": "h2bbbbbb", "handle": "u@x.example", "status": "active",
     "on_public_list": True},
    {"cluster_id": "text:a", "account_hash": "h3cccccc", "handle": "telegram:rybar", "status": "external"},
    {"cluster_id": "server:x.example", "account_hash": "h2bbbbbb", "handle": "u@x.example",
     "status": "active"},
]  # fmt: skip


def build(with_accounts: bool, reviewer: bool = False) -> dict[str, Any]:
    graph = Graph()
    add_clusters(graph, CLUSTERS, MEMBERS, LISTS, with_accounts=with_accounts, reviewer=reviewer)
    return graph.as_dict()


def node(data: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(n for n in data["nodes"] if n["id"] == node_id)


def test_clusters_link_to_where_their_accounts_post_and_what_they_link() -> None:
    data = build(with_accounts=False)
    ids = {n["id"] for n in data["nodes"]}
    assert {
        "cluster:text:a",
        "platform:bluesky",
        "server:x.example",
        "platform:telegram",
        "telegram:rybar",
    } <= ids
    assert not any(n["kind"] == "account" for n in data["nodes"])  # overview: no account nodes
    assert node(data, "website:fr.news-pravda.com")["listed"] is True and "website:bsky.app" not in ids
    assert node(data, "cluster:text:a")["layer"] == 2 and node(data, "platform:bluesky")["layer"] == 1
    assert (
        node(data, "telegram:rybar")["layer"] == 0
        and node(data, "cluster:text:a")["url"] == "/clusters/text:a"
    )
    pushes = [link for link in data["links"] if link["kind"] == "pushes"]
    assert pushes and pushes[0]["source"] == "telegram:rybar"


def test_account_names_only_for_reviewers_and_public_cases() -> None:
    public = build(with_accounts=True)
    assert node(public, "account:h1aaaaaa")["label"] == "account h1aaaa"  # not public
    assert node(public, "account:h2bbbbbb")["label"] == "u@x.example"  # on IFTAS's public list
    assert node(public, "account:h3cccccc")["label"] == "telegram:rybar"  # public channel
    assert node(build(with_accounts=True, reviewer=True), "account:h1aaaaaa")["label"] == "bot1.bsky.social"


def test_shared_accounts_and_reused_pictures_join_clusters() -> None:
    graph = Graph()
    add_clusters(graph, CLUSTERS, MEMBERS, LISTS, with_accounts=False, reviewer=False)
    add_cluster_links(
        graph,
        [{"a": "server:x.example", "b": "text:a", "n": 2}, {"a": "text:a", "b": "text:gone", "n": 5}],
        [{"earlier_cluster": "server:x.example", "later_cluster": "text:a", "supports": "earlier"}],
    )
    kinds = {(link["kind"], link["label"]) for link in graph.as_dict()["links"]}
    assert ("shared_accounts", "2 accounts in both") in kinds
    assert ("same_picture", "same picture: fits only the earlier caption") in kinds
    assert not any("text:gone" in (link["source"], link["target"]) for link in graph.as_dict()["links"])


STORY = {
    "id": 7,
    "title": "Fighting near Tersyanka",
    "evidence": {
        "headlines": {"H1": {"source": "TASS (state media; unofficial mirror on pravda.me)"},
                      "H2": {"source": "Telegram channel @rybar (republished by the Pravda network)"}},
        "news": {"N1": {"outlet": "Kyiv Independent", "listed": False},
                 "N2": {"outlet": "Pravda EN", "listed": True}},
        "clusters": {"S1": "text:a", "S2": "server:x.example"},
    },
    "report": {
        "contradictions": [{"about": "facts", "statement": "who holds the village", "refs": ["H1", "N1"]}],
        "agreements": [{"statement": "there is fighting", "refs": ["H1", "N1", "N2"]}],
        "amplification": [{"ref": "S1", "pushes": "state_media_version", "version": "TASS version"},
                          {"ref": "S2", "pushes": "unrelated", "version": ""}],
    },
}  # fmt: skip


def test_story_shows_versions_contradictions_and_who_pushes_them() -> None:
    graph = Graph()
    add_clusters(graph, CLUSTERS, MEMBERS, LISTS, with_accounts=False, reviewer=False)
    add_story(graph, STORY)
    add_campaign(graph, {"id": 1, "title": "Begging network", "cluster_ids": ["text:a", "text:missing"]})
    data = graph.as_dict()
    links = {(link["source"], link["target"], link["kind"]) for link in data["links"]}
    assert ("state:TASS", "outlet:kyiv independent", "contradicts") in links
    assert ("state:TASS", "outlet:pravda en", "agrees") in links
    assert ("state:TASS", "cluster:text:a", "pushes") in links
    assert not any(t == "cluster:server:x.example" and k == "pushes" for _, t, k in links)  # unrelated
    assert node(data, "telegram:rybar")["kind"] == "telegram"  # the same channel node as its cluster's
    assert node(data, "outlet:pravda en")["label"].endswith("(on a propaganda list)")
    assert ("campaign:1", "cluster:text:a", "part_of") in links
    assert node(data, "story:7")["url"] == "/stories/7"
