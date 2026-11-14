"""Public propaganda lists used as provenance evidence (downloaded by eval/ground_truth/fetch.py)."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

BRIDGY_SUFFIX = ".ap.brid.gy"


def bridged_server(handle: str | None) -> str | None:
    """Mastodon server of a Bridgy Fed account: 'user.mast.example.com.ap.brid.gy' -> 'mast.example.com'."""
    if not handle or not handle.endswith(BRIDGY_SUFFIX):
        return None
    rest = handle.removesuffix(BRIDGY_SUFFIX)
    return rest.split(".", 1)[1] if "." in rest else None


@dataclass
class KnownLists:
    domains: dict[str, str] = field(default_factory=dict)  # domain -> list name
    pravda_channels: set[str] = field(default_factory=set)  # lower-case Telegram channel names
    account_dids: set[str] = field(default_factory=set)
    account_handles: set[str] = field(default_factory=set)
    did_handles: dict[str, str] = field(default_factory=dict)  # listed DID -> its Bluesky handle
    servers: set[str] = field(default_factory=set)  # Mastodon servers on the IFTAS list
    loaded: bool = False

    @classmethod
    def load(cls, directory: Path) -> KnownLists:
        lists = cls()
        files = {
            name: directory / f"{name}.csv"
            for name in ("domains", "telegram_channels", "accounts", "instances")
        }
        missing = [str(p) for p in files.values() if not p.is_file()]
        if missing:
            log.warning("ground-truth lists missing; provenance signals disabled", extra={"missing": missing})
            return lists
        for row in _rows(files["domains"]):
            lists.domains.setdefault(row["domain"].lower(), row["list"])
        lists.pravda_channels = {row["channel"].lower() for row in _rows(files["telegram_channels"])}
        for row in _rows(files["accounts"]):
            if row.get("did"):
                lists.account_dids.add(row["did"])
                if row.get("bluesky_handle"):
                    lists.did_handles[row["did"]] = row["bluesky_handle"].lower()
            if row.get("bluesky_handle"):
                lists.account_handles.add(row["bluesky_handle"].lower())
        lists.servers = {row["instance"].lower() for row in _rows(files["instances"])}
        lists.loaded = True
        log.info(
            "ground-truth lists loaded",
            extra={
                "domains": len(lists.domains),
                "pravda_channels": len(lists.pravda_channels),
                "accounts": len(lists.account_handles),
                "servers": len(lists.servers),
            },
        )
        return lists

    def domain_hits(self, domains: list[str]) -> list[str]:
        """Listed domains among `domains` (subdomains of a listed domain count)."""
        hits = []
        for domain in domains:
            parts = domain.lower().removeprefix("www.").split(".")
            if any(".".join(parts[i:]) in self.domains for i in range(len(parts) - 1)):
                hits.append(domain)
        return hits

    def channel_hits(self, urls: list[str]) -> list[str]:
        hits = []
        for url in urls:
            parts = urlsplit(url)
            if (parts.hostname or "").removeprefix("www.") in ("t.me", "telegram.me"):
                path = parts.path.strip("/").split("/")
                channel = path[1] if path and path[0] == "s" and len(path) > 1 else (path[0] if path else "")
                if channel.lower() in self.pravda_channels:
                    hits.append(channel)
        return hits

    def is_known_account(self, did: str | None, handle: str | None) -> bool:
        return (did is not None and did in self.account_dids) or (
            handle is not None and handle.lower() in self.account_handles
        )

    def on_listed_server(self, handle: str | None) -> bool:
        server = bridged_server(handle)
        return server is not None and server.lower() in self.servers


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
