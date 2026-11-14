"""Run the stage-1 detector over recorded stream files and report what it would flag.

    python -m factslens.detector.replay data/recordings/events-20261113-1*.jsonl.gz [--top 15]

Prints aggregates and the shared text/links of the largest candidates; never account ids.
Used to tune the gates in factslens.detector.groups on real data (see docs/EVAL.md).
"""

from __future__ import annotations

import argparse
import glob
import time
import tracemalloc
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from factslens.detector.groups import GroupKind, GroupSnapshot, SwarmDetector
from factslens.ingest.sinks import read_jsonl

HOUR_US = 3_600_000_000


def hour_label(time_us: int) -> str:
    return datetime.fromtimestamp(time_us / 1e6, UTC).strftime("%a %H:00")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", help="recording files (globs allowed)")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--memory", action="store_true", help="measure peak memory (slower)")
    args = parser.parse_args()
    paths = sorted({Path(p) for pattern in args.files for p in glob.glob(pattern)})

    if args.memory:
        tracemalloc.start()
    detector = SwarmDetector()
    latest: dict[str, GroupSnapshot] = {}
    new_per_hour: dict[str, Counter[GroupKind]] = {}
    snapshots = 0
    started = time.perf_counter()
    first_us = last_us = None
    for path in paths:
        for event in read_jsonl(path):
            first_us = first_us or event.time_us
            last_us = event.time_us
            for snap in detector.add(event):
                snapshots += 1
                if snap.key not in latest:
                    new_per_hour.setdefault(hour_label(snap.last_us), Counter())[snap.kind] += 1
                latest[snap.key] = snap
    wall = time.perf_counter() - started
    if first_us is None or last_us is None:
        raise SystemExit("no events in the given files")

    hours = (last_us - first_us) / HOUR_US
    print(
        f"{detector.events:,} events over {hours:.1f} h of stream in {wall:.0f} s "
        f"({detector.events / wall:,.0f}/s)"
    )
    if args.memory:
        _, peak = tracemalloc.get_traced_memory()
        print(f"peak traced memory: {peak / 2**20:.0f} MiB; live state: {detector.memory_summary()}")
    kinds = Counter(s.kind for s in latest.values())
    print(
        f"candidates: {len(latest)} ({dict(kinds)}), {len(latest) / hours:.0f}/h; "
        f"snapshots written: {snapshots}"
    )
    print("new candidates per hour:")
    for hour, counts in new_per_hour.items():
        print(f"  {hour}  " + "  ".join(f"{k.value}={counts.get(k, 0)}" for k in GroupKind))

    for kind in GroupKind:
        group = sorted((s for s in latest.values() if s.kind is kind), key=lambda s: -s.accounts)
        if not group:
            continue
        print(f"\nlargest {kind.value} candidates:")
        for snap in group[: args.top]:
            stats = snap.stats
            label = " ".join(snap.label.split())[:90]
            print(
                f"  {snap.accounts:5d} acc {snap.events:6d} ev  burst {stats['burst_share_5m']:.2f}"
                f"  copy {stats.get('copy_share', 0):.2f}  bridged {stats['bridged_share']:.2f}"
                f"  ev/acc {stats['events_per_account']:5.1f}  {label}"
            )


if __name__ == "__main__":
    main()
