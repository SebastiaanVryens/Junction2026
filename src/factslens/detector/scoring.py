"""Stage 2: turn a candidate's evidence into explainable signals, a score and a tier.

Every signal carries its measured value, its threshold and a plain-language explanation, so a
reviewer sees *why* something was flagged. Signals fall into three categories:

- coordination: the accounts act together (copied text, synchrony, rings, overlap)
- account:      the accounts look inauthentic (new, created together, empty, templated, removed)
- provenance:   the cluster touches documented propaganda infrastructure (public lists)

Coordination alone is not suspicious: people sharing a news story with the share button's
pre-filled headline are coordinated by the button. A cluster is only "suspicious" when
coordination comes with account or provenance evidence. Thresholds are starting values and are
evaluated in docs/EVAL.md.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from factslens.detector import text as textsim


class Category(StrEnum):
    COORDINATION = "coordination"
    ACCOUNT = "account"
    PROVENANCE = "provenance"
    DISCLOSURE = "disclosure"  # what the accounts say about themselves


class Tier(StrEnum):
    PENDING = "pending"  # account profiles not fetched yet
    LIKELY_ORGANIC = "likely_organic"
    WATCH = "watch"
    SUSPICIOUS = "suspicious"
    DECLARED_AUTOMATION = "declared_automation"  # coordinated, but the accounts say they are bots


@dataclass(frozen=True)
class Profile:
    available: bool  # False: Bluesky no longer serves the account (suspended or deleted)
    handle: str | None = None
    created_at: datetime | None = None
    followers: int | None = None
    follows: int | None = None
    has_avatar: bool | None = None
    description: str | None = None


@dataclass(frozen=True)
class Evidence:
    kind: str  # text | link | repost
    stats: dict[str, float]  # stage-1 statistics
    profiles: Sequence[Profile | None]  # one per member; None = not fetched yet
    other_clusters: Sequence[int]  # per member: other candidate clusters in the last 24 h
    now: datetime
    known_accounts: int = 0  # members on the IFTAS list
    listed_server_members: int = 0  # members bridged from a Mastodon server on the IFTAS list
    known_domains: Sequence[str] = ()  # linked domains on VIGINUM / CheckFirst lists
    pravda_channels: Sequence[str] = ()  # linked Telegram channels the Pravda sites republish
    lists_loaded: bool = True


@dataclass(frozen=True)
class Signal:
    name: str
    category: Category
    value: float
    threshold: float
    fired: bool
    weight: int
    explanation: str
    unit: str = "share"  # share (0..1) or count

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Assessment:
    signals: list[Signal]
    score: int
    tier: Tier
    profile_coverage: float
    reasons: list[str] = field(default_factory=list)


MIN_PROFILE_COVERAGE = 0.6
DECLARED_SCORE_CAP = 20
# Words accounts use to say they are automated (in their bio or handle). Several languages,
# because disclosed bots post in many. Found on live data 14 Nov 2026: weather-alert and
# traffic-information bots ("Unofficial bot sharing alerts from NWS ...", "*Automated feed*").
AUTOMATION_WORDS = re.compile(
    r"(?<![\w-])(bot|robot|automated|automatic(?:ally)?|auto[- ]?post(?:ed|s|ing)?|rss|"
    r"not monitored|unmonitored|automatisch|automatisiert|automático|automatico|automatique|"
    r"automaattinen|бот|автоматическ\w*)(?![\w-])",
    re.IGNORECASE,
)
NEW_ACCOUNT_DAYS = 30
CREATION_BURST_HOURS = 72
BIO_MIN_CHARS = 20
BIO_SIMILARITY = 0.5


def _pct(value: float) -> str:
    return f"{value:.0%}"


def _share_signal(
    name: str, category: Category, value: float, threshold: float, weight: int, text: str
) -> Signal:
    return Signal(name, category, round(value, 3), threshold, value >= threshold, weight, text)


def creation_burst_share(created: Sequence[datetime], hours: int = CREATION_BURST_HOURS) -> float:
    """Largest share of accounts created within any `hours` stretch."""
    if not created:
        return 0.0
    times = sorted(created)
    span = timedelta(hours=hours)
    best = start = 0
    for end, t in enumerate(times):
        while t - times[start] > span:
            start += 1
        best = max(best, end - start + 1)
    return best / len(times)


def template_bio_share(descriptions: Sequence[str | None]) -> float:
    """Share of accounts whose bio near-duplicates another account's bio in the cluster."""
    bios = [textsim.normalise(d or "") for d in descriptions]
    sigs = [textsim.signature(b) if len(b) >= BIO_MIN_CHARS else None for b in bios]
    usable = [s for s in sigs if s is not None]
    if len(usable) < 2:
        return 0.0
    matched = 0
    for i, a in enumerate(usable):
        if any(i != j and textsim.similarity(a, b) >= BIO_SIMILARITY for j, b in enumerate(usable)):
            matched += 1
    return matched / len(sigs)


def coordination_signals(evidence: Evidence) -> list[Signal]:
    stats = evidence.stats
    signals = [
        _share_signal(
            "copy_paste",
            Category.COORDINATION,
            stats.get("copy_share", 0.0),
            0.5,
            15,
            f"{_pct(stats.get('copy_share', 0.0))} of posts repeat near-identical text from another account.",
        ),
        _share_signal(
            "synchrony",
            Category.COORDINATION,
            stats.get("burst_share_1m", 0.0),
            0.5,
            15,
            f"{_pct(stats.get('burst_share_1m', 0.0))} of the activity happened within a single minute.",
        ),
    ]
    if evidence.kind == "repost":
        ring = stats.get("ring_share", 0.0)
        signals.append(
            _share_signal(
                "repost_ring",
                Category.COORDINATION,
                ring,
                0.8,
                20,
                f"{_pct(ring)} of the accounts also reposted at least two other posts together.",
            )
        )
    repeats = stats.get("events_per_account", 1.0)
    signals.append(
        Signal(
            "repetition",
            Category.COORDINATION,
            repeats,
            2.0,
            repeats >= 2.0,
            10,
            f"Each account posted this {repeats:.1f} times on average.",
            unit="count",
        )
    )
    members = len(evidence.other_clusters)
    overlap = sum(n >= 2 for n in evidence.other_clusters) / members if members else 0.0
    signals.append(
        _share_signal(
            "cross_cluster",
            Category.COORDINATION,
            overlap,
            0.5,
            15,
            f"{_pct(overlap)} of the accounts also appear in at least two other flagged clusters (24 h).",
        )
    )
    return signals


def account_signals(evidence: Evidence) -> list[Signal]:
    fetched = [p for p in evidence.profiles if p is not None]
    available = [p for p in fetched if p.available]
    if not fetched:
        return []
    signals: list[Signal] = []
    gone = 1 - len(available) / len(fetched)
    signals.append(
        _share_signal(
            "removed_accounts",
            Category.ACCOUNT,
            gone,
            0.2,
            15,
            f"{_pct(gone)} of the accounts are no longer served by Bluesky (suspended or deleted).",
        )
    )
    if not available:
        return signals
    created = [p.created_at for p in available if p.created_at is not None]
    new = sum((evidence.now - c) <= timedelta(days=NEW_ACCOUNT_DAYS) for c in created) / len(available)
    signals.append(
        _share_signal(
            "new_accounts",
            Category.ACCOUNT,
            new,
            0.5,
            20,
            f"{_pct(new)} of the accounts were created in the last {NEW_ACCOUNT_DAYS} days.",
        )
    )
    burst = creation_burst_share(created) if len(created) >= 3 else 0.0
    signals.append(
        _share_signal(
            "creation_burst",
            Category.ACCOUNT,
            burst,
            0.4,
            20,
            f"{_pct(burst)} of the accounts were created within the same {CREATION_BURST_HOURS} hours.",
        )
    )
    low = sum((p.followers or 0) < 10 for p in available) / len(available)
    signals.append(
        _share_signal(
            "low_reach",
            Category.ACCOUNT,
            low,
            0.6,
            10,
            f"{_pct(low)} of the accounts have fewer than 10 followers.",
        )
    )
    empty = sum(not p.has_avatar or not (p.description or "").strip() for p in available) / len(available)
    signals.append(
        _share_signal(
            "empty_profiles",
            Category.ACCOUNT,
            empty,
            0.5,
            10,
            f"{_pct(empty)} of the accounts have no profile picture or no bio.",
        )
    )
    templated = template_bio_share([p.description for p in available])
    signals.append(
        _share_signal(
            "template_bios",
            Category.ACCOUNT,
            templated,
            0.3,
            15,
            f"{_pct(templated)} of the accounts have a bio that near-duplicates another account's bio.",
        )
    )
    return signals


def declares_automation(profile: Profile) -> bool:
    handle_name = (profile.handle or "").split(".")[0]
    return bool(AUTOMATION_WORDS.search(profile.description or "") or handle_name.lower().endswith("bot"))


def disclosure_signals(evidence: Evidence) -> list[Signal]:
    available = [p for p in evidence.profiles if p is not None and p.available]
    if not available:
        return []
    declared = sum(declares_automation(p) for p in available) / len(available)
    return [
        Signal(
            "declared_automation",
            Category.DISCLOSURE,
            round(declared, 3),
            0.5,
            declared >= 0.5,
            0,
            f"{_pct(declared)} of the accounts say in their profile that they are bots or automated feeds.",
        )
    ]


def provenance_signals(evidence: Evidence) -> list[Signal]:
    if not evidence.lists_loaded:
        return []
    members = max(len(evidence.profiles), 1)
    listed_share = evidence.listed_server_members / members

    def count_signal(name: str, value: int, weight: int, text: str) -> Signal:
        return Signal(name, Category.PROVENANCE, value, 1, value >= 1, weight, text, unit="count")

    domains = ", ".join(evidence.known_domains[:3])
    channels = ", ".join(evidence.pravda_channels[:3])
    return [
        count_signal(
            "known_accounts",
            evidence.known_accounts,
            30,
            f"{evidence.known_accounts} account(s) are on IFTAS's public list of suspected "
            "Portal Kombat accounts.",
        ),
        _share_signal(
            "listed_servers",
            Category.PROVENANCE,
            listed_share,
            0.3,
            25,
            f"{_pct(listed_share)} of the accounts are bridged from Mastodon servers on IFTAS's list.",
        ),
        count_signal(
            "known_domains",
            len(evidence.known_domains),
            30,
            f"Links to {len(evidence.known_domains)} domain(s) on VIGINUM/CheckFirst propaganda lists"
            + (f": {domains}." if domains else "."),
        ),
        count_signal(
            "pravda_sources",
            len(evidence.pravda_channels),
            10,
            f"Links to {len(evidence.pravda_channels)} Telegram channel(s) that Pravda sites republish"
            + (f": {channels}" if channels else "")
            + " (a lead, not proof).",
        ),
    ]


def assess(evidence: Evidence) -> Assessment:
    fetched = sum(p is not None for p in evidence.profiles)
    coverage = fetched / len(evidence.profiles) if evidence.profiles else 0.0
    signals = (
        coordination_signals(evidence)
        + account_signals(evidence)
        + provenance_signals(evidence)
        + disclosure_signals(evidence)
    )
    fired = [s for s in signals if s.fired]
    score = min(100, sum(s.weight for s in fired))
    coordination = any(s.category is Category.COORDINATION for s in fired)
    account = sum(s.category is Category.ACCOUNT for s in fired)
    provenance = any(s.category is Category.PROVENANCE for s in fired)
    declared = any(s.category is Category.DISCLOSURE for s in fired)

    reasons: list[str] = []
    if provenance and coordination:
        tier = Tier.SUSPICIOUS
        reasons.append("Coordinated activity that touches documented propaganda infrastructure.")
    elif declared:
        tier = Tier.DECLARED_AUTOMATION
        score = min(score, DECLARED_SCORE_CAP)
        reasons.append(
            "The accounts act together and look automated, but say so openly (e.g. weather or "
            "traffic alert bots). Disclosed automation is not inauthentic; a known-propaganda "
            "link would override this."
        )
    elif coverage < MIN_PROFILE_COVERAGE:
        tier = Tier.PENDING
        reasons.append(f"Only {_pct(coverage)} of account profiles fetched so far; account evidence pending.")
    elif coordination and account >= 2:
        tier = Tier.SUSPICIOUS
        reasons.append("Coordinated activity by accounts with several signs of being inauthentic.")
    elif (coordination and account == 1) or provenance:
        tier = Tier.WATCH
        reasons.append("Some evidence, not enough to call it inauthentic.")
    else:
        tier = Tier.LIKELY_ORGANIC
        reasons.append(
            "Accounts act alike, but look like ordinary established accounts: "
            "this is how organic sharing (e.g. a share button's headline) looks too."
        )
    return Assessment(
        signals=signals, score=score, tier=tier, profile_coverage=round(coverage, 3), reasons=reasons
    )
