"""Unit tests for the ground-truth parsers. Run: uv run --with pytest --with httpx pytest eval/ground_truth"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch  # noqa: E402


def test_refang_and_normalise() -> None:
    assert fetch.refang(" WWW.Piter-News[.]net ") == "piter-news.net"
    assert fetch.refang("lenta[.]kharkiv[.]ua") == "lenta.kharkiv.ua"


def test_parse_viginum_csv_handles_bom_and_defanging() -> None:
    text = "﻿domain,create date,first seen,type\npiter-news[.]net,07/03/2013,,domain\n,,,\n"
    rows = fetch.parse_viginum_csv(text, "domain", "create date", "viginum_portal_kombat")
    assert rows == [{"domain": "piter-news.net", "list": "viginum_portal_kombat", "created": "07/03/2013"}]


def test_parse_viginum_csv_rejects_changed_format() -> None:
    with pytest.raises(fetch.SourceFormatError):
        fetch.parse_viginum_csv("name,date\nx,y\n", "domain", "create date", "viginum_portal_kombat")


def test_bridgy_handle_matches_bridgy_fed_pattern() -> None:
    # Pattern documented in CheckFirst's Roska Bridge report, e.g. @majee.todon.ploud.fr.ap.brid.gy
    assert fetch.bridgy_handle("todon.ploud.fr", "Majee") == "majee.todon.ploud.fr.ap.brid.gy"


def test_parse_iftas_accounts_without_header_row() -> None:
    rows = [f"https://example.social/@user{i},DNIorAUD" for i in range(120)]
    rows.append("not a profile url,Contacted")
    parsed = fetch.parse_iftas_accounts("\n".join(rows))
    assert len(parsed) == 120
    assert parsed[0] == {
        "mastodon_url": "https://example.social/@user0",
        "instance": "example.social",
        "username": "user0",
        "bluesky_handle": "user0.example.social.ap.brid.gy",
        "iftas_status": "DNIorAUD",
    }


def test_parse_iftas_accounts_accepts_hyphenated_usernames() -> None:
    rows = [f"https://mast.example.com/@ab-cd{i},Limited" for i in range(120)]
    parsed = fetch.parse_iftas_accounts("\n".join(rows))
    assert len(parsed) == 120
    assert parsed[0]["bluesky_handle"] == "ab-cd0.mast.example.com.ap.brid.gy"


def test_parse_iftas_accounts_rejects_changed_format() -> None:
    with pytest.raises(fetch.SourceFormatError):
        fetch.parse_iftas_accounts("intro text only\nmore text\n")


def test_telegram_sources_keeps_only_telegram_channels() -> None:
    stats = {
        "topSources": [
            {"source": "Telegram: RTenfrancais", "count": 10},
            {"source": "telegram: @llordofwar", "count": 3},
            {"source": "rt.com", "count": 99},
        ]
    }
    assert fetch.telegram_sources(stats) == {"rtenfrancais": 10, "llordofwar": 3}


def test_merge_domains_keeps_one_row_per_list() -> None:
    rows = [
        {"domain": "a.com", "list": "x", "created": "1"},
        {"domain": "a.com", "list": "x", "created": "2"},
        {"domain": "a.com", "list": "y", "created": ""},
    ]
    assert [(r["domain"], r["list"]) for r in fetch.merge_domains(rows)] == [("a.com", "x"), ("a.com", "y")]
