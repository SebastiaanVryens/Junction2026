"""A contested story as a newspaper article: evidence ids become numbered footnotes.

Nemotron cites H ids (state media and Telegram posts) and N ids (independent news articles). On
the page they become footnotes ¹ ² ³ in order of first use, linked to a numbered source list;
sources the report never cites are listed after them. S ids (our groups of accounts) link to their
cluster; I and V ids (pictures, video) point at the pictures section.
"""

from __future__ import annotations

import re
from typing import Any

from markupsafe import Markup, escape

REF = re.compile(r"[HNSIV]\d{1,3}")


def _cited(report: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("versions", "contradictions", "agreements", "only_in_state_media"):
        for item in report.get(key) or []:
            refs += [r for ref in item.get("refs") or [] for r in REF.findall(str(ref))]
    return list(dict.fromkeys(refs))


def sources(evidence: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    """Every H and N source, numbered: cited ones first, in order of first use."""
    headlines = evidence.get("headlines") or {}
    news = evidence.get("news") or {}
    known = {
        **{r: ("source", h) for r, h in headlines.items()},
        **{r: ("outlet", a) for r, a in news.items()},
    }
    order = [r for r in _cited(report) if r in known] + [r for r in known if r not in _cited(report)]
    listed = []
    for n, ref in enumerate(order, start=1):
        kind, item = known[ref]
        url = str(item.get("url") or item.get("link") or "")
        if kind == "source":
            name, text = str(item.get("source") or "State media or Telegram"), str(item.get("text") or "")
        else:
            name, text = (
                str(item.get("outlet") or item.get("site") or "News outlet"),
                str(item.get("title") or ""),
            )
        listed.append(
            {
                "n": n,
                "ref": ref,
                "kind": kind,
                "name": name,
                "text": " ".join(text.split())[:220],
                "url": url if url.startswith("https://") else "",
                "language": item.get("language"),
                "published": str(item.get("published") or item.get("time") or "")[:10],
                "listed": bool(item.get("listed")),
                "cited": ref in _cited(report),
            }
        )
    return listed


def footnotes(sources_: list[dict[str, Any]], clusters: dict[str, str]) -> dict[str, dict[str, str]]:
    """ref -> how the page shows it: a footnote number, a group link, or a picture."""
    notes: dict[str, dict[str, str]] = {s["ref"]: {"kind": "note", "n": str(s["n"])} for s in sources_}
    for ref, cluster_id in clusters.items():
        notes[ref] = {"kind": "group", "url": f"/clusters/{cluster_id}"}
    return notes


def _runs(numbers: list[int]) -> list[tuple[int, int]]:
    """[1, 2, 3, 5, 7, 8] -> [(1, 3), (5, 5), (7, 8)]."""
    runs: list[tuple[int, int]] = []
    for n in sorted(set(numbers)):
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return runs


def footnote(refs: list[str], notes: dict[str, dict[str, str]]) -> Markup:
    """How a statement cites its evidence in print: superscript source numbers, with runs written
    as ranges (1-7), plus links to the groups of accounts and to the pictures it relies on."""
    found = [r for ref in refs for r in REF.findall(str(ref))]
    numbers = [int(notes[r]["n"]) for r in found if notes.get(r, {}).get("kind") == "note"]
    parts = []
    if numbers:
        links = [
            f'<a href="#src-{a}" title="Source {a}">{a}</a>'
            if a == b
            else f'<a href="#src-{a}" title="Sources {a} to {b}">{a}\u2013{b}</a>'
            for a, b in _runs(numbers)
        ]
        parts.append('<sup class="fn">' + ",".join(links) + "</sup>")
    groups = list(dict.fromkeys(notes[r]["url"] for r in found if notes.get(r, {}).get("kind") == "group"))
    parts += [f' <a class="fn-group" href="{escape(url)}" title="A group of accounts we found">[group]</a>'
              for url in groups]  # fmt: skip
    kinds = list(dict.fromkeys("video" if r[0] == "V" else "picture" for r in found if r[0] in "IV"))
    parts += [f' <a class="fn-group" href="#pictures">[{kind}]</a>' for kind in kinds]
    return Markup("".join(parts))


def counts(evidence: dict[str, Any]) -> dict[str, int]:
    """How many sources were compared: state media and Telegram posts, and distinct outlets."""
    news = (evidence.get("news") or {}).values()
    return {
        "state": len(evidence.get("headlines") or {}),
        "outlets": len({str(a.get("site") or a.get("outlet") or "") for a in news if isinstance(a, dict)}),
    }
