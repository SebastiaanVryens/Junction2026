"""Offline evaluation: replay recorded Bluesky traffic with injected red-team campaigns through the
same stage 1 (detector) and stage 2 (profiles + scoring) code that runs in production.

    python -m factslens.eval.run --start 2026-11-13T15:00 --hours 3 \\
        --campaigns eval/campaigns/redteam-v1.json --out eval/results/ [--save-db]

What is measured (every number states its unit and sample size):
- campaign recall per difficulty: a campaign counts as caught when one cluster holding at least
  half of its accounts reaches the tier (unit: campaign);
- time to detection: minutes from the campaign's first post to that cluster becoming a candidate;
- alert load on real traffic: real-traffic clusters per tier, per hour of stream (unit: cluster).
  Without human labels these are not called false positives: some are real spam networks;
- the same two quantities across score thresholds.
"""

from __future__ import annotations

import argparse
import heapq
import json
import logging
import subprocess
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from factslens.common.logging import configure_logging
from factslens.common.models import Event
from factslens.common.settings import get_settings
from factslens.detector.groups import GATES, GroupSnapshot, SwarmDetector
from factslens.detector.scoring import Evidence, Profile, assess
from factslens.enrich.lists import KnownLists
from factslens.enrich.profiles import ProfileClient
from factslens.eval.redteam import CampaignSet, account_id, to_events, to_profiles
from factslens.ingest.sinks import read_jsonl

log = logging.getLogger("factslens.eval.run")

HOUR_US = 3_600_000_000
COVERAGE = 0.5  # share of a campaign's accounts one cluster must hold to count as catching it
TIER_RANK = {"likely_organic": 0, "declared_automation": 0, "pending": 1, "watch": 2, "suspicious": 3}
THRESHOLDS = list(range(0, 101, 10))


def recording_files(directory: Path, start: datetime, hours: float) -> list[Path]:
    """Hourly files `events-YYYYMMDD-HH.jsonl.gz` that overlap [start, start + hours)."""
    wanted = []
    hour = start.replace(minute=0, second=0, microsecond=0)
    while hour < start + timedelta(hours=hours):
        path = directory / f"events-{hour:%Y%m%d-%H}.jsonl.gz"
        if path.exists():
            wanted.append(path)
        hour += timedelta(hours=1)
    return wanted


def recorded_events(files: list[Path], start_us: int, end_us: int) -> Iterator[Event]:
    for path in files:
        for event in read_jsonl(path):
            if start_us <= event.time_us < end_us:
                yield event


class ProfileCache:
    """Real Bluesky profiles for accounts in candidate clusters, cached on disk between runs."""

    def __init__(self, path: Path, client: ProfileClient) -> None:
        self.path = path
        self.client = client
        self.data: dict[str, dict[str, Any] | None] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                self.data[row["did"]] = row["profile"]

    def get_many(self, dids: list[str]) -> dict[str, Profile]:
        missing = [d for d in dids if d not in self.data]
        if missing:
            fetched = self.client.fetch(missing)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for did in missing:
                    p = fetched.get(did)
                    stored: dict[str, Any] | None = None
                    if p is not None:
                        stored = {
                            "handle": p.handle,
                            "created_at": p.created_at.isoformat() if p.created_at else None,
                            "followers": p.followers,
                            "follows": p.follows,
                            "has_avatar": p.has_avatar,
                            "description": p.description,
                        }
                    self.data[did] = stored
                    handle.write(json.dumps({"did": did, "profile": stored}) + "\n")
        result: dict[str, Profile] = {}
        for did in dids:
            row = self.data.get(did)
            if row is None:
                result[did] = Profile(available=False)
            else:
                created = datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None
                result[did] = Profile(
                    available=True,
                    handle=row.get("handle"),
                    created_at=created,
                    followers=row.get("followers"),
                    follows=row.get("follows"),
                    has_avatar=row.get("has_avatar"),
                    description=row.get("description"),
                )
        return result


@dataclass
class Scored:
    snapshot: GroupSnapshot
    candidate_at_us: int
    tier: str
    score: int
    fired: list[str]
    synthetic_accounts: int


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return "unknown"


def run(
    start: datetime, hours: float, campaign_set: CampaignSet, recordings: Path, cache: ProfileCache
) -> dict[str, Any]:
    start_us = int(start.timestamp() * 1_000_000)
    end_us = start_us + int(hours * HOUR_US)
    files = recording_files(recordings, start, hours)
    if not files:
        raise SystemExit(f"no recordings for {start:%Y-%m-%d %H:%M} + {hours} h in {recordings}")

    # Inject campaigns evenly across the window, leaving the first and last stretch clear.
    campaigns = campaign_set.campaigns
    spacing = (end_us - start_us) // (len(campaigns) + 1)
    starts = [start_us + spacing * (i + 1) for i in range(len(campaigns))]
    synthetic: list[Event] = []
    synthetic_profiles: dict[str, Profile] = {}
    campaign_accounts: list[set[str]] = []
    for i, campaign in enumerate(campaigns):
        events = to_events(campaign, i, starts[i])
        synthetic += events
        synthetic_profiles |= to_profiles(campaign, i, datetime.fromtimestamp(starts[i] / 1e6, UTC))
        campaign_accounts.append(
            {account_id(i, p) for p in range(len(campaign.personas))} & {e.author for e in events}
        )
    synthetic.sort(key=lambda e: e.time_us)

    detector = SwarmDetector()
    latest: dict[str, GroupSnapshot] = {}
    first_candidate: dict[str, int] = {}
    real_events = 0
    stream = heapq.merge(recorded_events(files, start_us, end_us), synthetic, key=lambda e: e.time_us)
    for event in stream:
        real_events += not event.synthetic
        for snap in detector.add(event):
            latest[snap.key] = snap
            first_candidate.setdefault(snap.key, snap.last_us)
    log.info("stage 1 done", extra={"events": detector.events, "candidates": len(latest)})

    # Stage 2: profiles (real ones fetched and cached; synthetic from personas) and scoring.
    real_members = sorted({a for s in latest.values() for a in s.members if a not in synthetic_profiles})
    profiles = cache.get_many(real_members) | synthetic_profiles
    memberships: Counter[str] = Counter(a for s in latest.values() for a in s.members)
    settings = get_settings()
    lists = KnownLists.load(settings.ground_truth_dir)
    now = datetime.fromtimestamp(end_us / 1e6, UTC)
    scored: list[Scored] = []
    for key, snap in latest.items():
        members = list(snap.members)
        linked = [d for d, _ in snap.domains]
        if snap.kind.value == "link" and (host := urlsplit(snap.label).hostname):
            linked.append(host)
        if snap.kind.value == "domain":
            linked.append(snap.label)
        evidence = Evidence(
            kind=snap.kind.value,
            stats=snap.stats,
            profiles=[profiles.get(a) for a in members],
            other_clusters=[memberships[a] - 1 for a in members],
            now=now,
            known_accounts=sum(
                lists.is_known_account(a, (profiles.get(a) or Profile(False)).handle) for a in members
            ),
            listed_server_members=sum(
                lists.on_listed_server((profiles.get(a) or Profile(False)).handle) for a in members
            ),
            known_domains=sorted(set(lists.domain_hits(linked))),
            pravda_channels=sorted(
                set(lists.channel_hits([snap.label] if snap.kind.value == "link" else []))
            ),
            lists_loaded=lists.loaded,
        )
        result = assess(evidence)
        scored.append(
            Scored(
                snap,
                first_candidate[key],
                result.tier.value,
                result.score,
                [s.name for s in result.signals if s.fired],
                sum(a in synthetic_profiles for a in members),
            )
        )

    # Campaign metrics (unit: campaign).
    per_campaign = []
    for i, (campaign, accounts) in enumerate(zip(campaigns, campaign_accounts, strict=True)):
        hits = [s for s in scored if accounts & set(s.snapshot.members)]
        covering = [s for s in hits if len(accounts & set(s.snapshot.members)) >= COVERAGE * len(accounts)]
        best = max(covering, key=lambda s: (TIER_RANK[s.tier], s.score), default=None)
        per_campaign.append(
            {
                "index": i,
                "difficulty": campaign.difficulty,
                "narrative": campaign.narrative[:140],
                "accounts": len(accounts),
                "posts": len(campaign.posts),
                "clusters_touching": len(hits),
                "best_tier": best.tier if best else "missed",
                "best_score": best.score if best else 0,
                "coverage": round(len(accounts & set(best.snapshot.members)) / len(accounts), 2)
                if best
                else 0.0,
                "minutes_to_candidate": round((best.candidate_at_us - starts[i]) / 60e6, 1) if best else None,
                "fired": best.fired if best else [],
            }
        )

    def recall(rows: list[dict[str, Any]], tiers: set[str]) -> dict[str, Any]:
        caught = sum(r["best_tier"] in tiers for r in rows)
        return {"caught": caught, "n": len(rows), "share": round(caught / len(rows), 3) if rows else None}

    by_difficulty: dict[str, Any] = {}
    for difficulty in ("simple", "varied", "evasive", "all"):
        rows = [r for r in per_campaign if difficulty in ("all", r["difficulty"])]
        by_difficulty[difficulty] = {
            "suspicious": recall(rows, {"suspicious"}),
            "suspicious_or_watch": recall(rows, {"suspicious", "watch"}),
            "candidate": recall(rows, set(TIER_RANK)),
        }

    # Real traffic (unit: cluster without any synthetic account).
    organic = [s for s in scored if s.synthetic_accounts == 0]
    organic_tiers = Counter(s.tier for s in organic)
    per_hour = {tier: round(n / hours, 1) for tier, n in organic_tiers.items()}
    sweep = []
    for t in THRESHOLDS:
        caught = sum(
            any(len(acc & set(s.snapshot.members)) >= COVERAGE * len(acc) and s.score >= t for s in scored)
            for acc in campaign_accounts
        )
        flagged = sum(s.score >= t for s in organic)
        sweep.append(
            {
                "score_at_least": t,
                "campaign_recall": round(caught / len(campaigns), 3) if campaigns else None,
                "campaigns_n": len(campaigns),
                "real_clusters_flagged_per_hour": round(flagged / hours, 1),
            }
        )
    examples = sorted((s for s in organic if s.tier == "suspicious"), key=lambda s: -s.score)[:15]
    return {
        "run": {
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "git_commit": git_commit(),
            "window_start": start.isoformat(),
            "hours": hours,
            "recording_files": [p.name for p in files],
            "real_events": real_events,
            "synthetic_events": len(synthetic),
            "campaign_set": campaign_set.prompt_version,
            "campaign_model": campaign_set.model,
            "gates": {
                k.value: {"min_accounts": g.min_accounts, "any_of": g.any_of} for k, g in GATES.items()
            },
            "coverage_rule": f"one cluster holds >= {COVERAGE:.0%} of the campaign's accounts",
        },
        "campaign_recall": by_difficulty,
        "campaigns": per_campaign,
        "real_traffic": {
            "clusters": len(organic),
            "by_tier": dict(organic_tiers),
            "per_hour_by_tier": per_hour,
            "profiles_unavailable": sum(1 for a in real_members if not profiles[a].available),
            "accounts_checked": len(real_members),
        },
        "threshold_sweep": sweep,
        "suspicious_real_examples": [
            {
                "kind": s.snapshot.kind.value,
                "label": " ".join(s.snapshot.label.split())[:160],
                "accounts": s.snapshot.accounts,
                "score": s.score,
                "fired": s.fired,
            }
            for s in examples
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline evaluation with red-team campaigns")
    parser.add_argument("--start", required=True, help="window start, UTC, e.g. 2026-11-13T15:00")
    parser.add_argument("--hours", type=float, default=3.0)
    parser.add_argument("--campaigns", type=Path, default=Path("eval/campaigns/redteam-v1.json"))
    parser.add_argument("--out", type=Path, default=Path("eval/results"))
    parser.add_argument("--save-db", action="store_true", help="store the result for the metrics page")
    args = parser.parse_args()
    settings = get_settings()
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    campaign_set = CampaignSet.model_validate_json(args.campaigns.read_text(encoding="utf-8"))
    cache = ProfileCache(settings.data_dir / "eval_cache" / "profiles.jsonl", ProfileClient())
    result = run(start, args.hours, campaign_set, settings.data_dir / "recordings", cache)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"run-{start:%Y%m%d-%H%M}-{args.hours:g}h.json"
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"written {out}")
    print(json.dumps(result["campaign_recall"], indent=1))
    print(json.dumps(result["real_traffic"], indent=1))
    if args.save_db:
        import psycopg
        from psycopg.types.json import Jsonb

        from factslens.common import db

        with psycopg.connect(db.database_url(settings), autocommit=True) as connection:
            connection.execute(
                "INSERT INTO eval_runs (name, result) VALUES (%s, %s)", (out.stem, Jsonb(result))
            )
        print("stored in eval_runs")


if __name__ == "__main__":
    configure_logging()
    main()
