# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""Download the public ground-truth lists FactsLens uses for provenance and evaluation.

Usage (from the repo root):
    uv run eval/ground_truth/fetch.py                     # all lists
    uv run eval/ground_truth/fetch.py --resolve-bluesky   # also look up each IFTAS account on Bluesky

Writes to eval/ground_truth/data/ (git-ignored; the sources' licences don't all allow
redistribution, and the account list names real accounts):

    domains.csv            domain, list, created, source
    telegram_channels.csv  channel, pravda_articles, pravda_domains
    accounts.csv           mastodon_url, instance, username, bluesky_handle, iftas_status[, did, bluesky_status]
    instances.csv          instance, iftas_observations
    manifest.json          per source: url, fetched_at, rows, sha256 of the raw download, licence note

Sources (verified 14 Nov 2026, see CLAUDE.md §9):
    - VIGINUM Portal Kombat + RRN/Doppelgänger domain CSVs (GitHub, TLP:CLEAR, no licence file)
    - CheckFirst pravda-network dashboard stats (GitHub): Pravda subdomains and the Telegram
      channels each subdomain republishes from
    - IFTAS "Suspected Portal Kombat Accounts" public Google Sheet (no licence stated)

Not fetched: American Sunlight Project spreadsheet (download format not checked yet).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

OUT_DIR = Path(__file__).resolve().parent / "data"
TIMEOUT = httpx.Timeout(60.0)
USER_AGENT = "FactsLens-ground-truth-fetch/0.1 (Junction 2026 research project)"

VIGINUM_RAW = "https://raw.githubusercontent.com/VIGINUM-FR/Rapports-Techniques/main"
PORTAL_KOMBAT_CSV = f"{VIGINUM_RAW}/202402%20-%20Portal%20Kombat/20241227_SGDSN_VIGINUM_NP_TLP-CLEAR_Portal-Kombat-domains.csv"
RRN_CSV = f"{VIGINUM_RAW}/202306%20-%20RRN/NDD_RRN.csv"
VIGINUM_LICENCE = "VIGINUM report data, TLP:CLEAR; no licence file in the repo"

CHECKFIRST_TREE = "https://api.github.com/repos/CheckFirstHQ/pravda-network/git/trees/HEAD?recursive=1"
CHECKFIRST_RAW = "https://raw.githubusercontent.com/CheckFirstHQ/pravda-network/main"
CHECKFIRST_LICENCE = "CheckFirst pravda-network repo; no licence file"

IFTAS_SHEET = "https://docs.google.com/spreadsheets/d/1dsklopIabZOR6OkHrNS9mLnO5cfAZRbBoExD3sHowKY/export"
IFTAS_ACCOUNTS_GID = "1926542286"
IFTAS_INSTANCES_GID = "1554967480"
IFTAS_LICENCE = "IFTAS public sheet; no licence stated, do not redistribute"

APPVIEW = "https://public.api.bsky.app/xrpc"
RESOLVE_CONCURRENCY = 8

# Some fediverse servers allow hyphens in usernames (e.g. Roska Bridge's @epowo-uzata on mast.qixto.com).
MASTODON_PROFILE = re.compile(
    r"^https?://(?P<instance>[^/\s]+)/@(?P<username>[A-Za-z0-9_][A-Za-z0-9_\-]*)/?$"
)
TELEGRAM_SOURCE = re.compile(r"^\s*telegram:\s*@?(?P<channel>[A-Za-z0-9_]{4,})\s*$", re.IGNORECASE)


class SourceFormatError(RuntimeError):
    """A source no longer has the shape this script expects; fix the parser, don't guess."""


@dataclass
class Manifest:
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(self, name: str, url: str, raw: bytes | None, rows: int, licence: str) -> None:
        self.entries[name] = {
            "url": url,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "rows": rows,
            "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "licence": licence,
        }


# --------------------------------------------------------------------------- parsing (pure)


def refang(domain: str) -> str:
    """VIGINUM publishes defanged domains like 'piter-news[.]net'."""
    return domain.strip().lower().replace("[.]", ".").removeprefix("www.")


def parse_viginum_csv(
    text: str, domain_column: str, created_column: str, list_name: str
) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None or domain_column not in reader.fieldnames:
        raise SourceFormatError(f"{list_name}: expected column {domain_column!r}, got {reader.fieldnames}")
    rows = []
    for row in reader:
        domain = refang(row[domain_column])
        if domain:
            rows.append({"domain": domain, "list": list_name, "created": row.get(created_column, "").strip()})
    return rows


def bridgy_handle(instance: str, username: str) -> str:
    """Bridgy Fed's Bluesky handle for a Mastodon account: user@instance -> user.instance.ap.brid.gy.
    Handles cannot contain underscores, so Bridgy writes them as hyphens (checked 14 Nov 2026: with
    underscores all 226 such IFTAS accounts looked absent from Bluesky; with hyphens some resolve)."""
    return f"{username.replace('_', '-')}.{instance}.ap.brid.gy".lower()


def parse_iftas_accounts(text: str) -> list[dict[str, str]]:
    """The accounts tab has no header row: column 0 = Mastodon profile URL, column 1 = IFTAS status."""
    rows = []
    skipped = 0
    for record in csv.reader(io.StringIO(text)):
        if not record or not record[0].strip():
            continue
        match = MASTODON_PROFILE.match(record[0].strip())
        if not match:
            skipped += 1
            continue
        instance, username = match["instance"].lower(), match["username"]
        rows.append(
            {
                "mastodon_url": record[0].strip(),
                "instance": instance,
                "username": username,
                "bluesky_handle": bridgy_handle(instance, username),
                "iftas_status": record[1].strip() if len(record) > 1 else "",
            }
        )
    if len(rows) < 100 or skipped > len(rows) * 0.05:
        raise SourceFormatError(f"IFTAS accounts: {len(rows)} profile URLs parsed, {skipped} rows skipped")
    return rows


def parse_iftas_instances(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "All Domains" not in reader.fieldnames:
        raise SourceFormatError(f"IFTAS instances: unexpected header {reader.fieldnames}")
    return [
        {
            "instance": row["All Domains"].strip().lower(),
            "iftas_observations": row.get("Total Active Observations", "").strip(),
        }
        for row in reader
        if row["All Domains"].strip()
    ]


def telegram_sources(stats: dict[str, Any]) -> Counter[str]:
    """Telegram channels a Pravda subdomain republishes from, weighted by article count."""
    channels: Counter[str] = Counter()
    for source in stats.get("topSources", []):
        match = TELEGRAM_SOURCE.match(str(source.get("source", "")))
        if match:
            channels[match["channel"].lower()] += int(source.get("count", 0))
    return channels


def merge_domains(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """One row per (domain, list); a domain may be on several lists."""
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        seen.setdefault((row["domain"], row["list"]), row)
    return sorted(seen.values(), key=lambda r: (r["domain"], r["list"]))


# --------------------------------------------------------------------------- downloading


async def get(client: httpx.AsyncClient, url: str, **params: str) -> httpx.Response:
    for attempt in range(4):
        response = await client.get(url, params=params or None)
        if response.status_code not in {429, 500, 502, 503, 504}:
            response.raise_for_status()
            return response
        await asyncio.sleep(2**attempt)
    response.raise_for_status()
    return response


async def fetch_viginum(client: httpx.AsyncClient, manifest: Manifest) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name, url, domain_col, created_col in (
        ("viginum_portal_kombat", PORTAL_KOMBAT_CSV, "domain", "create date"),
        ("viginum_rrn", RRN_CSV, "domain name", "created on"),
    ):
        response = await get(client, url)
        parsed = parse_viginum_csv(response.text, domain_col, created_col, name)
        manifest.record(name, url, response.content, len(parsed), VIGINUM_LICENCE)
        rows += [r | {"source": url} for r in parsed]
    return rows


async def fetch_checkfirst(
    client: httpx.AsyncClient, manifest: Manifest
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    tree = (await get(client, CHECKFIRST_TREE)).json()
    paths = [
        t["path"]
        for t in tree.get("tree", [])
        if t["path"].startswith("json/") and t["path"].endswith("_viz.json")
    ]
    if len(paths) < 20:
        raise SourceFormatError(f"CheckFirst: only {len(paths)} stats files found")

    semaphore = asyncio.Semaphore(8)

    async def one(path: str) -> tuple[bytes, dict[str, Any]]:
        async with semaphore:
            response = await get(client, f"{CHECKFIRST_RAW}/{path}")
            return response.content, response.json()

    results = await asyncio.gather(*(one(p) for p in paths))
    digest = hashlib.sha256()
    domains: list[dict[str, str]] = []
    channel_articles: Counter[str] = Counter()
    channel_domains: dict[str, set[str]] = {}
    for raw, stats in results:
        digest.update(raw)
        domain = refang(str(stats.get("domain", "")))
        if not domain:
            raise SourceFormatError("CheckFirst: stats file without 'domain'")
        domains.append(
            {"domain": domain, "list": "checkfirst_pravda", "created": "", "source": CHECKFIRST_RAW}
        )
        for channel, count in telegram_sources(stats).items():
            channel_articles[channel] += count
            channel_domains.setdefault(channel, set()).add(domain)

    channels = [
        {"channel": c, "pravda_articles": str(n), "pravda_domains": str(len(channel_domains[c]))}
        for c, n in channel_articles.most_common()
    ]
    manifest.entries["checkfirst_pravda"] = {
        "url": CHECKFIRST_TREE,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": len(domains),
        "telegram_channels": len(channels),
        "sha256": digest.hexdigest(),
        "licence": CHECKFIRST_LICENCE,
    }
    return domains, channels


async def fetch_iftas(
    client: httpx.AsyncClient, manifest: Manifest
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    accounts_resp = await get(client, IFTAS_SHEET, format="csv", gid=IFTAS_ACCOUNTS_GID)
    accounts = parse_iftas_accounts(accounts_resp.text)
    manifest.record(
        "iftas_accounts", str(accounts_resp.url), accounts_resp.content, len(accounts), IFTAS_LICENCE
    )

    instances_resp = await get(client, IFTAS_SHEET, format="csv", gid=IFTAS_INSTANCES_GID)
    instances = parse_iftas_instances(instances_resp.text)
    manifest.record(
        "iftas_instances", str(instances_resp.url), instances_resp.content, len(instances), IFTAS_LICENCE
    )
    return accounts, instances


async def resolve_bluesky(client: httpx.AsyncClient, accounts: list[dict[str, str]]) -> Counter[str]:
    """Add did + bluesky_status (active | suspended | deactivated | not_found | error:<code>) per account."""
    semaphore = asyncio.Semaphore(RESOLVE_CONCURRENCY)

    async def call(method: str, **params: str) -> httpx.Response:
        async with semaphore:
            for attempt in range(5):
                response = await client.get(f"{APPVIEW}/{method}", params=params)
                if response.status_code != 429 and response.status_code < 500:
                    return response
                await asyncio.sleep(2**attempt)
            return response

    async def one(account: dict[str, str]) -> None:
        resolved = await call("com.atproto.identity.resolveHandle", handle=account["bluesky_handle"])
        if resolved.status_code != 200:
            account |= {
                "did": "",
                "bluesky_status": "not_found"
                if resolved.status_code == 400
                else f"error:{resolved.status_code}",
            }
            return
        did = resolved.json()["did"]
        profile = await call("app.bsky.actor.getProfile", actor=did)
        if profile.status_code == 200:
            status = "active"
        else:
            error = (
                profile.json().get("error", "")
                if profile.headers.get("content-type", "").startswith("application/json")
                else ""
            )
            status = {"AccountTakedown": "suspended", "AccountDeactivated": "deactivated"}.get(
                error, f"error:{profile.status_code}"
            )
        account |= {"did": did, "bluesky_status": status}

    await asyncio.gather(*(one(a) for a in accounts))
    return Counter(a["bluesky_status"] for a in accounts)


# --------------------------------------------------------------------------- output


def write_csv(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


async def run(resolve: bool) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = Manifest()
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        viginum, (pravda_domains, channels), (accounts, instances) = await asyncio.gather(
            fetch_viginum(client, manifest), fetch_checkfirst(client, manifest), fetch_iftas(client, manifest)
        )
        statuses = await resolve_bluesky(client, accounts) if resolve else None

    domains = merge_domains([*viginum, *pravda_domains])
    write_csv(OUT_DIR / "domains.csv", domains, ["domain", "list", "created", "source"])
    write_csv(OUT_DIR / "telegram_channels.csv", channels, ["channel", "pravda_articles", "pravda_domains"])
    account_columns = ["mastodon_url", "instance", "username", "bluesky_handle", "iftas_status"]
    write_csv(
        OUT_DIR / "accounts.csv", accounts, account_columns + (["did", "bluesky_status"] if resolve else [])
    )
    write_csv(OUT_DIR / "instances.csv", instances, ["instance", "iftas_observations"])
    if statuses is not None:
        manifest.entries["iftas_accounts"]["bluesky_status"] = dict(statuses)
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest.entries, indent=2), encoding="utf-8")

    by_list = Counter(d["list"] for d in domains)
    print(f"domains.csv            {len(domains)} rows  {dict(by_list)}")
    print(f"telegram_channels.csv  {len(channels)} channels Pravda republishes from")
    print(
        f"accounts.csv           {len(accounts)} IFTAS accounts"
        + (f"  Bluesky: {dict(statuses)}" if statuses else "")
    )
    print(f"instances.csv          {len(instances)} Mastodon instances")
    print(f"written to {OUT_DIR}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Download FactsLens ground-truth lists.")
    parser.add_argument(
        "--resolve-bluesky",
        action="store_true",
        help="look up every IFTAS account on Bluesky (DID + active/suspended); ~2 AppView calls per account",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.resolve_bluesky))
    except SourceFormatError as error:
        print(f"source format changed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
