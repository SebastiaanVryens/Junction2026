"""The layered provenance graph behind /graph: where content came from, who posts close to it, and
who contradicts it.

Layers, top to bottom (the page fixes each node's height by its layer):
  0 sources    state media, Telegram channels, websites, independent news outlets
  1 platforms  Bluesky, each fediverse server, Telegram
  2 clusters   our clusters (tier colours), plus story and campaign hubs
  3 accounts   only in focused views; names only for reviewers and already-public cases

Lines: cluster -> platform/server (where its accounts post), cluster -> website (what it links),
cluster <-> cluster (shared accounts, or the same picture with another caption), state-media
source -> cluster that pushes its version, and source <-> outlet where a story found that they
contradict or agree. Built from rows the repository returns; no database access here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from factslens.enrich.lists import KnownLists, account_server

LAYERS = {"source": 0, "platform": 1, "cluster": 2, "account": 3}
TIER_COLOURS = {
    "suspicious": "#dc2626",
    "watch": "#f97316",
    "pending": "#9ca3af",
    "likely_organic": "#16a34a",
    "declared_automation": "#2563eb",
}
COLOURS = {
    "state_media": "#b91c1c",
    "telegram": "#0ea5e9",
    "website": "#a3a3a3",
    "website_listed": "#ef4444",
    "outlet": "#22c55e",
    "platform": "#e5e7eb",
    "story": "#eab308",
    "campaign": "#a855f7",
    "account": "#94a3b8",
    "account_listed": "#f43f5e",
}
LINK_COLOURS = {
    "posts_on": "#64748b",
    "links_to": "#a3a3a3",
    "shared_accounts": "#a855f7",
    "same_picture": "#f59e0b",
    "pushes": "#ef4444",
    "contradicts": "#ff1744",
    "agrees": "#22c55e",
    "member": "#475569",
    "part_of": "#3b3f4a",  # faint: context, not a finding
}
PLATFORM_DOMAINS = {"bsky.app", "t.me", "telegram.me", "youtube.com", "youtu.be", "x.com", "twitter.com"}
MAX_DOMAINS_PER_CLUSTER = 2


@dataclass
class Graph:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    links: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)

    def node(self, node_id: str, kind: str, layer: str, label: str, **extra: Any) -> str:
        if node_id not in self.nodes:
            self.nodes[node_id] = {
                "id": node_id,
                "kind": kind,
                "layer": LAYERS[layer],
                "label": label[:160],
                "color": extra.pop("color", COLOURS.get(kind, "#9ca3af")),
                "size": extra.pop("size", 2),
                **extra,
            }
        return node_id

    def link(self, source: str, target: str, kind: str, label: str = "", weight: int = 1) -> None:
        if source == target:
            return
        key = (source, target, kind)
        if key in self.links:
            self.links[key]["weight"] += weight
            return
        self.links[key] = {
            "source": source,
            "target": target,
            "kind": kind,
            "label": label[:200],
            "color": LINK_COLOURS[kind],
            "weight": weight,
        }

    def as_dict(self) -> dict[str, Any]:
        return {"nodes": list(self.nodes.values()), "links": list(self.links.values())}


def platform_node(graph: Graph, handle: str | None) -> str:
    """Where an account posts: a Telegram channel, a fediverse server, or Bluesky itself."""
    if handle and handle.startswith("telegram:"):
        return graph.node("platform:telegram", "platform", "platform", "Telegram", size=6)
    server = account_server(handle)
    if server:
        return graph.node(f"server:{server}", "platform", "platform", f"Mastodon server {server}", size=4)
    return graph.node("platform:bluesky", "platform", "platform", "Bluesky", size=8)


def add_clusters(
    graph: Graph,
    clusters: list[dict[str, Any]],
    members: list[dict[str, Any]],
    lists: KnownLists,
    *,
    with_accounts: bool,
    reviewer: bool,
    only_listed_websites: bool = False,
) -> None:
    by_cluster: dict[str, list[dict[str, Any]]] = {}
    for m in members:
        by_cluster.setdefault(m["cluster_id"], []).append(m)
    for c in clusters:
        cid = graph.node(
            f"cluster:{c['id']}",
            "cluster",
            "cluster",
            f"{c['kind']}: {' '.join(str(c['label']).split())}",
            color=TIER_COLOURS.get(c.get("tier") or "pending", "#9ca3af"),
            size=max(2, min(30, int(c.get("accounts") or 1))),
            tier=c.get("tier"),
            accounts=c.get("accounts"),
            url=f"/clusters/{c['id']}",
        )
        # where its accounts post, weighted by how many accounts post there
        places: Counter[str] = Counter()
        for m in by_cluster.get(c["id"], []):
            handle = m.get("handle")
            if handle and handle.startswith("telegram:"):
                channel = handle.removeprefix("telegram:")
                source = graph.node(
                    f"telegram:{channel}", "telegram", "source", f"Telegram channel @{channel}",
                    size=5, url=f"https://t.me/{channel}",
                )  # fmt: skip
                graph.link(source, cid, "pushes", "posted by this Telegram channel")
            places[platform_node(graph, handle)] += 1
            if with_accounts:
                public = bool(m.get("on_public_list")) or m.get("status") in ("unavailable", "external")
                name = handle if (reviewer or public) and handle else f"account {m['account_hash'][:6]}"
                aid = graph.node(
                    f"account:{m['account_hash']}",
                    "account",
                    "account",
                    name,
                    color=COLOURS["account_listed"] if m.get("on_public_list") else COLOURS["account"],
                    size=1,
                    listed=bool(m.get("on_public_list")),
                )
                graph.link(aid, cid, "member", "posted in this cluster")
        for place, n in places.items():
            graph.link(cid, place, "posts_on", f"{n} account(s) post here", weight=n)
        # what it links to
        domains = [d for d, _ in (c.get("domains") or []) if d and d not in PLATFORM_DOMAINS]
        if c["kind"] == "domain":
            domains = [str(c["label"]), *domains]
        for domain in list(dict.fromkeys(domains))[:MAX_DOMAINS_PER_CLUSTER]:
            listed = bool(lists.domain_hits([domain]))
            if only_listed_websites and not listed:
                continue
            wid = graph.node(
                f"website:{domain}",
                "website",
                "source",
                domain + (" (on a propaganda list)" if listed else ""),
                color=COLOURS["website_listed" if listed else "website"],
                size=4 if listed else 2,
                listed=listed,
            )
            graph.link(cid, wid, "links_to", "links to this website")


def add_cluster_links(graph: Graph, shared: list[dict[str, Any]], reuse: list[dict[str, Any]]) -> None:
    for row in shared:
        a, b = f"cluster:{row['a']}", f"cluster:{row['b']}"
        if a in graph.nodes and b in graph.nodes:
            graph.link(a, b, "shared_accounts", f"{row['n']} accounts in both", weight=int(row["n"]))
    for row in reuse:
        a, b = f"cluster:{row['earlier_cluster']}", f"cluster:{row['later_cluster']}"
        if a in graph.nodes and b in graph.nodes:
            fits = {"earlier": "fits only the earlier caption", "later": "fits the later caption"}
            graph.link(a, b, "same_picture", "same picture: " + fits.get(row.get("supports") or "", "reused"))


def add_story(graph: Graph, story: dict[str, Any]) -> None:
    """A contested story: its sources, the outlets, the clusters that push a version, and who
    contradicts or agrees with whom."""
    report, evidence = story.get("report") or {}, story.get("evidence") or {}
    hub = graph.node(
        f"story:{story['id']}", "story", "cluster", f"Story: {story.get('title') or report.get('title', '')}",
        size=10, url=f"/stories/{story['id']}",
    )  # fmt: skip
    ref_node: dict[str, str] = {}
    for ref, h in (evidence.get("headlines") or {}).items():
        source = str(h.get("source", ""))
        name = source.split(" (")[0]
        if source.startswith("Telegram channel @"):
            channel = name.removeprefix("Telegram channel @")
            ref_node[ref] = graph.node(f"telegram:{channel.lower()}", "telegram", "source", name, size=5,
                                       url=f"https://t.me/{channel}")  # fmt: skip
        else:
            ref_node[ref] = graph.node(
                f"state:{name}", "state_media", "source", f"{name} (state media)", size=7
            )
        graph.link(ref_node[ref], hub, "part_of", "tells this story")
    for ref, a in (evidence.get("news") or {}).items():
        outlet = str(a.get("outlet") or a.get("site") or "outlet")
        listed = bool(a.get("listed"))
        ref_node[ref] = graph.node(
            f"outlet:{outlet.lower()}", "outlet", "source",
            outlet + (" (on a propaganda list)" if listed else ""),
            color=COLOURS["website_listed"] if listed else COLOURS["outlet"], size=3,
        )  # fmt: skip
        graph.link(ref_node[ref], hub, "part_of", "reports this story")
    for ref, cluster_id in (evidence.get("clusters") or {}).items():
        ref_node[ref] = f"cluster:{cluster_id}"
    for item in report.get("amplification") or []:
        if item.get("pushes", "unrelated") == "unrelated":
            continue
        cluster = ref_node.get(item.get("ref", ""))
        if cluster in graph.nodes:
            sides = [r for r in ref_node if r[0] == ("H" if item["pushes"] != "independent_version" else "N")]
            for side in sides[:3]:
                graph.link(ref_node[side], cluster, "pushes", f"pushes: {item.get('version', '')}")
            graph.link(hub, cluster, "part_of", "amplifies this story")
    for kind, key in (("contradicts", "contradictions"), ("agrees", "agreements")):
        for point in report.get(key) or []:
            refs = [r for r in point.get("refs", []) if r in ref_node]
            heads = [ref_node[r] for r in refs if r.startswith("H")]
            news = [ref_node[r] for r in refs if r.startswith("N")]
            for h in heads[:3]:
                for n in news[:4]:
                    graph.link(h, n, kind, f"{point.get('about', '')} {point.get('statement', '')}".strip())


DIM_CLUSTER = "#4b5563"
DIM_ACCOUNT = "#334155"


def dim_bystanders(graph: Graph, story: dict[str, Any]) -> None:
    """In a story's own view: clusters that only mention the story (matched by keyword, but the
    model found they do not push it) and their accounts are greyed out."""
    clusters = (story.get("evidence") or {}).get("clusters") or {}
    pushers = {
        f"cluster:{clusters[a['ref']]}"
        for a in (story.get("report") or {}).get("amplification") or []
        if a.get("pushes", "unrelated") != "unrelated" and a.get("ref") in clusters
    }
    for node in graph.nodes.values():
        if node["kind"] == "cluster" and node["id"] not in pushers:
            node["color"], node["dim"] = DIM_CLUSTER, True
            node["label"] += " (mentions it, does not push it)"
    dim_clusters = {n["id"] for n in graph.nodes.values() if n.get("dim")}
    for (source, target, kind), _ in graph.links.items():
        if kind == "member" and target in dim_clusters and source in graph.nodes:
            graph.nodes[source]["color"] = DIM_ACCOUNT


def add_campaign(graph: Graph, campaign: dict[str, Any]) -> None:
    hub = graph.node(
        f"campaign:{campaign['id']}", "campaign", "cluster", f"Campaign: {campaign.get('title') or ''}",
        size=12, url=f"/campaigns/{campaign['id']}",
    )  # fmt: skip
    for cluster_id in campaign.get("cluster_ids") or []:
        if f"cluster:{cluster_id}" in graph.nodes:
            graph.link(hub, f"cluster:{cluster_id}", "part_of", "part of this campaign")
