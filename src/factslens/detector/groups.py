"""Stage 1 of swarm detection: group posts that point at the same thing, in a sliding window.

Three kinds of group, each a different way accounts can act together:

- TEXT:   near-duplicate text from different accounts (copy-paste, possibly truncated)
- LINK:   the same link shared by different accounts
- REPOST: the same post reposted by many accounts

A group becomes a *candidate* when it passes a cheap gate (enough distinct accounts plus a
coordination hint). Candidates are emitted as `GroupSnapshot`s for stage 2 (account profiles
and scoring); nothing here decides that anything is inauthentic.

Everything runs on event time, so replaying a recording gives the same result as live.
Memory stays bounded: a group object only exists once a second post joins it, and expired
state leaves through time-ordered queues instead of full scans.
"""

from __future__ import annotations

import hashlib
from collections import Counter, deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from factslens.common.models import Event, EventKind
from factslens.detector import text as textsim
from factslens.detector.windows import MINUTE_US, link_domain

WINDOW_US = 60 * MINUTE_US
MATCH_SIMILARITY = 0.7  # estimated Jaccard of 5-char shingles to join a TEXT group
MAX_ITEMS_PER_GROUP = 2000
MAX_BUCKET = 200  # LSH buckets bigger than this are boilerplate ("gm", templates); not indexed further
MAX_RECENT_REPOSTS = 50  # per account, for the repost-ring measure
SAMPLE_POSTS = 25
SNAPSHOT_EVERY_US = 2 * MINUTE_US

TRACKING_PARAMS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src", "s", "si", "feature"}


class GroupKind(StrEnum):
    TEXT = "text"
    LINK = "link"
    REPOST = "repost"


@dataclass(frozen=True)
class Gate:
    """Pass when there are enough accounts and, if `any_of` is set, at least one listed
    statistic reaches its threshold."""

    min_accounts: int
    any_of: dict[str, float] = field(default_factory=dict)


# Starting values from recorded stream data (docs/EVAL.md). A gate only decides what is worth a
# closer look; scoring happens later, with account-level evidence.
GATES: dict[GroupKind, Gate] = {
    GroupKind.TEXT: Gate(min_accounts=4),
    GroupKind.LINK: Gate(min_accounts=5, any_of={"copy_share": 0.5, "burst_share_1m": 0.6}),
    # Viral posts are reposted fast by many unrelated people, so speed alone is no signal.
    # A ring is: the same accounts also repost the same *other* posts together.
    GroupKind.REPOST: Gate(min_accounts=10, any_of={"ring_share": 0.8}),
}


def normalise_url(url: str) -> str | None:
    """Canonical form of a link, so trivially different URLs to the same page group together."""
    domain = link_domain(url)
    if domain is None:
        return None
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", domain, path, urlencode(sorted(query)), ""))


def _short_hash(value: str) -> str:
    return hashlib.blake2b(value.encode(), digest_size=8).hexdigest()


@dataclass(frozen=True, slots=True)
class Item:
    time_us: int
    author: str
    post_id: str
    text: str = ""
    bridged: bool = False
    media: tuple[tuple[str, str], ...] = ()  # (kind, ref): image/video blob CIDs, link-card URLs
    links: tuple[str, ...] = ()


@dataclass
class Group:
    key: str
    kind: GroupKind
    label: str  # the shared text, URL or reposted post
    items: deque[Item] = field(default_factory=lambda: deque(maxlen=MAX_ITEMS_PER_GROUP))
    total_events: int = 0
    first_us: int = 0
    last_us: int = 0
    candidate_since_us: int | None = None
    last_snapshot_us: int | None = None
    last_snapshot_accounts: int = 0
    signatures: dict[str, textsim.Signature] = field(default_factory=dict)  # LINK: post id -> text sig

    def add(self, item: Item) -> None:
        if self.total_events == 0:
            self.first_us = item.time_us
        self.items.append(item)
        self.total_events += 1
        self.last_us = max(self.last_us, item.time_us)

    @property
    def accounts(self) -> int:
        return len({i.author for i in self.items})


@dataclass
class _Pending:
    """A link or reposted post seen from one account so far; becomes a Group on the second."""

    kind: GroupKind
    label: str
    items: list[Item]
    signatures: dict[str, textsim.Signature]
    last_us: int


@dataclass(frozen=True)
class GroupSnapshot:
    """What stage 2 receives about a candidate group. Contains account ids (needed to look up
    profiles); the API decides what is shown publicly."""

    key: str
    kind: GroupKind
    label: str
    first_us: int
    last_us: int
    events: int
    accounts: int
    stats: dict[str, float]
    members: dict[str, tuple[int, int, int]]  # author -> (events, first_us, last_us)
    sample: tuple[Item, ...]
    per_minute: tuple[int, ...]  # events per minute, oldest first, over the group's lifetime (max 60)
    domains: tuple[tuple[str, int], ...]
    languages: tuple[tuple[str, int], ...]


def burst_share(times: list[int], span_us: int) -> float:
    """Largest share of events inside any `span_us` stretch (1.0 = all at once)."""
    if not times:
        return 0.0
    times = sorted(times)
    best = start = 0
    for end, t in enumerate(times):
        while t - times[start] > span_us:
            start += 1
        best = max(best, end - start + 1)
    return best / len(times)


def copy_share(items: list[Item], signatures: dict[str, textsim.Signature]) -> float:
    """Share of posts whose text near-duplicates a post by a *different* account."""
    posts = [i for i in items if i.post_id in signatures]
    if len(posts) < 2:
        return 0.0
    copied = 0
    for i, a in enumerate(posts):
        sig_a = signatures[a.post_id]
        for j, b in enumerate(posts):
            if (
                i != j
                and a.author != b.author
                and textsim.similarity(sig_a, signatures[b.post_id]) >= MATCH_SIMILARITY
            ):
                copied += 1
                break
    return copied / len(posts)


class SwarmDetector:
    """Feed events in time order with `add`; collect candidate snapshots from its return value."""

    def __init__(self, gates: dict[GroupKind, Gate] | None = None) -> None:
        self.gates = gates or GATES
        self.groups: dict[str, Group] = {}
        self._pending: dict[str, _Pending] = {}
        self._expiry: deque[tuple[int, str]] = deque()  # (last seen, key) for groups and pending
        # TEXT index: LSH bucket -> [(post id, signature, item)]; post id -> its group, once grouped
        self._buckets: dict[tuple[int, bytes], list[tuple[str, textsim.Signature, Item]]] = {}
        self._indexed: deque[tuple[int, list[tuple[int, bytes]], str]] = deque()
        self._post_group: dict[str, str] = {}
        self._post_meta: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}  # langs, domains
        self._meta_order: deque[tuple[int, str]] = deque()
        # Repost-ring measure: account -> recent (time, group key) reposts
        self._recent_reposts: dict[str, deque[tuple[int, str]]] = {}
        self._repost_order: deque[tuple[int, str]] = deque()
        self.newest_us = 0
        self.events = 0

    # --- ingestion -------------------------------------------------------------------------

    def add(self, event: Event) -> list[GroupSnapshot]:
        self.events += 1
        self.newest_us = max(self.newest_us, event.time_us)
        touched: list[Group] = []
        if event.kind is EventKind.POST:
            touched += self._add_post(event)
        elif event.kind is EventKind.REPOST and event.subject:
            key = f"repost:{_short_hash(event.subject)}"
            item = Item(event.time_us, event.author, event.id)
            if group := self._add_keyed(key, GroupKind.REPOST, event.subject, item, None):
                touched.append(group)
            self._remember_repost(event.author, event.time_us, key)
        if self.events % 500 == 0:
            self._evict()
        return [s for g in touched if (s := self._maybe_snapshot(g)) is not None]

    def _add_keyed(
        self, key: str, kind: GroupKind, label: str, item: Item, sig: textsim.Signature | None
    ) -> Group | None:
        """LINK and REPOST: keep the first account's items aside until a second account arrives."""
        group = self.groups.get(key)
        if group is not None:
            group.add(item)
            if sig is not None:
                group.signatures[item.post_id] = sig
            return group
        pending = self._pending.get(key)
        if pending is None:
            pending = self._pending[key] = _Pending(kind, label, [], {}, item.time_us)
            self._expiry.append((item.time_us, key))
        pending.last_us = max(pending.last_us, item.time_us)
        if len(pending.items) < 50:
            pending.items.append(item)
            if sig is not None:
                pending.signatures[item.post_id] = sig
        if item.author == pending.items[0].author:
            return None
        del self._pending[key]
        group = self.groups[key] = Group(key=key, kind=kind, label=label, signatures=pending.signatures)
        for earlier in pending.items:
            group.add(earlier)
        if pending.items[-1] is not item:
            group.add(item)
        return group

    def _add_post(self, event: Event) -> list[Group]:
        touched: list[Group] = []
        normalised = textsim.normalise(event.text)
        item = Item(
            event.time_us,
            event.author,
            event.id,
            event.text[:300],
            event.origin_url is not None,
            tuple((m.kind.value, m.ref) for m in event.media[:4]),
            event.links[:5],
        )
        sig = textsim.signature(normalised) if textsim.is_substantial(normalised) else None
        urls = {u for link in event.links if (u := normalise_url(link))}
        domains = tuple(sorted({urlsplit(u).hostname or "" for u in urls}))
        self._post_meta[event.id] = (event.langs, domains)
        self._meta_order.append((event.time_us, event.id))

        if sig is not None and (group := self._add_text(item, sig)) is not None:
            touched.append(group)
        for url in urls:
            if group := self._add_keyed(f"link:{_short_hash(url)}", GroupKind.LINK, url, item, sig):
                touched.append(group)
        return touched

    def _add_text(self, item: Item, sig: textsim.Signature) -> Group | None:
        keys = textsim.band_keys(sig)
        best: tuple[float, str, Item] | None = None
        for key in keys:
            for post_id, other_sig, other in self._buckets.get(key, ()):
                score = textsim.similarity(sig, other_sig)
                if score >= MATCH_SIMILARITY and (best is None or score > best[0]):
                    best = (score, post_id, other)
        group: Group | None = None
        if best is not None:
            _, match_id, match = best
            group_key = self._post_group.get(match_id)
            group = self.groups.get(group_key) if group_key else None
            if group is None:  # the match was a lone post (or its group expired): start a group
                group_key = f"text:{_short_hash(match_id)}"
                group = self.groups[group_key] = Group(key=group_key, kind=GroupKind.TEXT, label=match.text)
                group.add(match)
                self._post_group[match_id] = group_key
                self._expiry.append((item.time_us, group_key))
            group.add(item)
            self._post_group[item.post_id] = group.key
        for key in keys:
            bucket = self._buckets.setdefault(key, [])
            if len(bucket) < MAX_BUCKET:
                bucket.append((item.post_id, sig, item))
        self._indexed.append((item.time_us, keys, item.post_id))
        return group

    def _remember_repost(self, author: str, time_us: int, key: str) -> None:
        recent = self._recent_reposts.get(author)
        if recent is None:
            recent = self._recent_reposts[author] = deque(maxlen=MAX_RECENT_REPOSTS)
            self._repost_order.append((time_us, author))
        recent.append((time_us, key))

    # --- statistics and candidates ---------------------------------------------------------

    def ring_share(self, group: Group) -> float:
        """Share of the group's accounts that also reposted at least two *other* posts which a
        sizeable part of the group reposted too (the same set of accounts amplifying together)."""
        members = {i.author for i in group.items}
        if len(members) < 3:
            return 0.0
        cutoff = self.newest_us - WINDOW_US
        subjects: dict[str, set[str]] = {}
        counts: Counter[str] = Counter()
        for author in members:
            mine = {k for t, k in self._recent_reposts.get(author, ()) if k != group.key and t >= cutoff}
            subjects[author] = mine
            counts.update(mine)
        needed = max(3, int(0.3 * len(members)))
        shared = {k for k, c in counts.items() if c >= needed}
        if not shared:
            return 0.0
        return sum(len(s & shared) >= 2 for s in subjects.values()) / len(members)

    def stats(self, group: Group) -> dict[str, float]:
        items = list(group.items)
        times = [i.time_us for i in items]
        accounts = len({i.author for i in items})
        result = {
            "accounts": float(accounts),
            "events": float(group.total_events),
            "events_per_account": round(len(items) / max(accounts, 1), 2),
            "burst_share_1m": round(burst_share(times, MINUTE_US), 3),
            "burst_share_5m": round(burst_share(times, 5 * MINUTE_US), 3),
            "bridged_share": round(len({i.author for i in items if i.bridged}) / max(accounts, 1), 3),
            "duration_minutes": round((group.last_us - group.first_us) / MINUTE_US, 1),
        }
        if group.kind is GroupKind.TEXT:
            result["copy_share"] = 1.0
        elif group.kind is GroupKind.LINK:
            result["copy_share"] = round(copy_share(items, group.signatures), 3)
        else:
            result["ring_share"] = round(self.ring_share(group), 3)
        return result

    def passes_gate(self, kind: GroupKind, stats: dict[str, float]) -> bool:
        gate = self.gates[kind]
        if stats["accounts"] < gate.min_accounts:
            return False
        return not gate.any_of or any(stats.get(name, 0.0) >= value for name, value in gate.any_of.items())

    def _maybe_snapshot(self, group: Group) -> GroupSnapshot | None:
        min_accounts = self.gates[group.kind].min_accounts
        if group.total_events < min_accounts or group.accounts < min_accounts:
            return None  # cheap early exit: most groups never get here
        now = group.last_us
        if group.last_snapshot_us is not None:  # a candidate already: throttle updates
            grew = group.accounts >= max(
                group.last_snapshot_accounts * 1.25, group.last_snapshot_accounts + 2
            )
            if not grew and now - group.last_snapshot_us < SNAPSHOT_EVERY_US:
                return None
        elif group.candidate_since_us is None and group.accounts == group.last_snapshot_accounts:
            return None  # gate already failed at this size; wait for a new account
        stats = self.stats(group)
        if group.candidate_since_us is None:
            if not self.passes_gate(group.kind, stats):
                group.last_snapshot_accounts = group.accounts
                return None
            group.candidate_since_us = now
        group.last_snapshot_us = now
        group.last_snapshot_accounts = group.accounts
        return self._snapshot(group, stats)

    def _snapshot(self, group: Group, stats: dict[str, float]) -> GroupSnapshot:
        members: dict[str, list[int]] = {}
        languages: Counter[str] = Counter()
        domains: Counter[str] = Counter()
        firsts: list[Item] = []
        for item in group.items:
            entry = members.get(item.author)
            if entry is None:
                members[item.author] = [1, item.time_us, item.time_us]
                firsts.append(item)  # one post per account first: the sample shows the spread
            else:
                entry[0] += 1
                entry[1] = min(entry[1], item.time_us)
                entry[2] = max(entry[2], item.time_us)
            langs, doms = self._post_meta.get(item.post_id, ((), ()))
            languages.update(langs)
            domains.update(doms)
        last_minute = group.last_us // MINUTE_US
        start = max(group.first_us // MINUTE_US, last_minute - 59)
        per_minute = Counter(i.time_us // MINUTE_US for i in group.items)
        return GroupSnapshot(
            key=group.key,
            kind=group.kind,
            label=group.label,
            first_us=group.first_us,
            last_us=group.last_us,
            events=group.total_events,
            accounts=len(members),
            stats=stats,
            members={a: (e[0], e[1], e[2]) for a, e in members.items()},
            sample=tuple(firsts[:SAMPLE_POSTS]),
            per_minute=tuple(per_minute.get(m, 0) for m in range(start, last_minute + 1)),
            domains=tuple(domains.most_common(10)),
            languages=tuple(languages.most_common(5)),
        )

    # --- memory ----------------------------------------------------------------------------

    def _evict(self) -> None:
        cutoff = self.newest_us - WINDOW_US
        requeue: list[tuple[int, str]] = []
        while self._expiry and self._expiry[0][0] < cutoff:
            _, key = self._expiry.popleft()
            entry = self.groups.get(key) or self._pending.get(key)
            if entry is None:
                continue
            if entry.last_us < cutoff:
                self.groups.pop(key, None)
                self._pending.pop(key, None)
            else:
                requeue.append((entry.last_us, key))
        self._expiry.extend(sorted(requeue))
        while self._indexed and self._indexed[0][0] < cutoff:
            _, band_keys, post_id = self._indexed.popleft()
            for band_key in band_keys:
                bucket = self._buckets.get(band_key)
                if bucket is not None:
                    bucket[:] = [entry for entry in bucket if entry[0] != post_id]
                    if not bucket:
                        del self._buckets[band_key]
            self._post_group.pop(post_id, None)
        while self._meta_order and self._meta_order[0][0] < cutoff:
            self._post_meta.pop(self._meta_order.popleft()[1], None)
        while self._repost_order and self._repost_order[0][0] < cutoff:
            _, author = self._repost_order.popleft()
            recent = self._recent_reposts.get(author)
            if recent and recent[-1][0] >= cutoff:
                self._repost_order.append((recent[-1][0], author))
            else:
                self._recent_reposts.pop(author, None)

    def memory_summary(self) -> dict[str, int]:
        return {
            "groups": len(self.groups),
            "pending": len(self._pending),
            "indexed_posts": len(self._indexed),
            "buckets": len(self._buckets),
            "reposting_accounts": len(self._recent_reposts),
            "candidates": sum(g.candidate_since_us is not None for g in self.groups.values()),
        }

    def candidates(self) -> Iterator[Group]:
        return (g for g in self.groups.values() if g.candidate_since_us is not None)
